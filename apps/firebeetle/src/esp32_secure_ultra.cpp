#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <math.h>
#include <mbedtls/gcm.h>
#include <mbedtls/md.h>
#include "crypto_key.h"
#include "sensor_adapter.h"

// Encrypted IMU / ACK / MODIFY / RESULT through an opaque PC relay.
// ArduinoJson 7: https://arduinojson.org/v7/tutorial/serialization/
// Network configuration: connect to the relay laptop on the same reachable LAN.
// In full pipeline mode the laptop forwards ciphertext; Ultra96 owns the host session keys.
const char* SSID = "cg4002-b19-2G";
const char* PASSWORD = "iopiop890890";
const char* PC_IP = "192.168.0.3";
constexpr uint16_t PORT = 5500;
// crypto_key.h supplies this board ID and its matching 256-bit PSK.
// Generate/upload the correct header separately for boards 1 and 2.
constexpr uint32_t DEVICE_ID = SECURE_DEVICE_ID;
constexpr size_t MAX_FRAME = 1024;

// TCP carries application-encrypted frames; WiFiClient itself does not provide TLS.
WiFiClient peer;
String rxBuffer;
// Boot ID identifies this power-on run, while a fresh challenge identifies each TCP session.
// nextSeq is the application counter shared by IMU and all response types.
char bootId[9];
uint32_t nextSeq = 0;
// Bounded in-flight IMU window: no per-frame serial output on the hot path.
struct Outstanding { uint32_t seq, at; };
// Ring buffer of sent IMUs awaiting ACK: head is the oldest entry, count is occupancy.
// The configured window never exceeds this 32-entry capacity.
Outstanding pending[32];
uint8_t head=0, count=0, windowSize=8;
uint32_t intervalMs=20, ackReceived=0;
// Require exactly two valid lowercase hex characters per expected output byte.
bool hexDecode(const char*,uint8_t*,size_t);
// Represent binary ciphertext, tags and file chunks as hex inside JSON.
// Hex encoding is transport representation, not encryption.
String hexEncode(const uint8_t*,size_t);
// CONFIG adjusts pacing/window; FILE_CHUNK returns one chunk from RAM.
// Both commands have already passed GCM and encrypted sequence verification.
void processExtra(JsonDocument&);
uint32_t lastConnect = 0, lastWifiRetry = 0, lastSend = 0;

// Separate direction keys prevent transmit and receive nonce spaces from sharing a key.
// txSeq/lastRx are cryptographic counters, independent of application nextSeq.
String boardChallenge;
uint8_t txKey[32],rxKey[32];
bool ready=false,haveRx=false;
uint32_t txSeq=0,lastRx=0,applied=0,authRejects=0,replayRejects=0,lastActivity=0;
// Dispatch authenticated application messages to ACK, configuration, file or MODIFY handling.
// MODIFY replies carry reply_to from the host command; their own seq belongs to this board.
void processFrame(const String& line);
// Encrypt and authenticate a compact application JSON document with AES-256-GCM.
// Enforce the 512-byte plaintext limit and reconnect before the cipher counter wraps.
bool sendEncrypted(JsonDocument& doc);


uint32_t lastStatusSent = 0;
void sendCalibrationStatus() {
    JsonDocument out;
    out["v"]=1; out["type"]="CAL_STATUS"; out["device_id"]=DEVICE_ID;
    out["boot_id"]=bootId; out["seq"]=nextSeq++; out["ts_ms"]=millis();
    out["state"]=calibrationName();
    sendEncrypted(out); lastStatusSent=millis();
}
void processCalibration(JsonDocument& c) {
    if (!c["v"].is<int>() || c["v"].as<int>() != 1 ||
        !c["device_id"].is<uint32_t>() || c["device_id"].as<uint32_t>() != DEVICE_ID ||
        !c["boot_id"].is<const char*>() || strcmp(c["boot_id"],bootId) ||
        !c["seq"].is<uint32_t>()) return;
    const char* action=c["action"] | "";
    bool ok = strlen(action)==1 && requestCalibration(action[0]);
    JsonDocument out;
    out["v"]=1; out["type"]="CAL_RESULT"; out["device_id"]=DEVICE_ID;
    out["boot_id"]=bootId; out["seq"]=nextSeq++; out["ts_ms"]=millis();
    out["reply_to"]=c["seq"].as<uint32_t>(); out["accepted"]=ok;
    out["state"]=calibrationName();
    sendEncrypted(out);
    sendCalibrationStatus();
}

const char* AXES[] = {"ax", "ay", "az", "gx", "gy", "gz"};

// Discard incomplete frames and outstanding IMU tracking, then reconnect through loop().
// Unconfirmed messages are not replayed automatically; memset clears the key arrays.
void resetConnection() {
    if (count) Serial.println("[UNCONFIRMED] Pending packet abandoned; no automatic replay");
    ready=false;
    memset(txKey,0,32);memset(rxKey,0,32);
    peer.stop();
    rxBuffer = "";
    head=0; count=0;
}

// Legacy partial-write helper; the current send path uses sendDoc() below.
// TCP write may accept fewer bytes than requested, so advance by the actual returned count.
bool writeAll(const String& wire) {
    size_t sent = 0;
    uint32_t start = millis();
    while (sent < wire.length() && peer.connected()) {
        if (millis() - start >= 3000) break;
        size_t n = peer.write(reinterpret_cast<const uint8_t*>(wire.c_str()) + sent,
                              wire.length() - sent);
        sent += n;
        if (!n) delay(1);
    }
    if (sent != wire.length()) {
        Serial.println("[TX ERROR] Incomplete write; reconnecting");
        resetConnection();
        return false;
    }
    return true;
}

// Send the newest real sample; sample_seq exposes skipped acquisitions.
// The host ACKs each authenticated IMU; only successfully sent frames enter the pending ring.
void sendImu() {
    if (!sensorQueue || calibrationState.load() != SENSOR_READY) return;
    SensorSample sample;
    if (xQueueReceive(sensorQueue, &sample, 0) != pdTRUE) return;
    if (millis() - sample.at > 100) return;
    lastSend = millis();
    JsonDocument doc;
    uint32_t sequence = nextSeq++;
    doc["v"] = 1;
    doc["type"] = "IMU";
    doc["device_id"] = DEVICE_ID;
    doc["boot_id"] = bootId;
    doc["seq"] = sequence;
    doc["ts_ms"] = sample.at;
    doc["sample_seq"] = sample.sampleSeq;
    doc["sensor_schema"] = "si_flex_v1";
    for (size_t i=0; i<6; ++i) doc["imu"][AXES[i]] = roundf(sample.imu[i]*1000.0f)/1000.0f;
    for (size_t i=0; i<5; ++i) doc["flex"][i] = roundf(sample.flex[i]*1000.0f)/1000.0f;
    String wire;
    serializeJson(doc, wire); // Compact JSON: exactly one line per message.
    if (wire.length() > MAX_FRAME) {
        Serial.println("[TX ERROR] Frame too long");
        return;
    }
    // This temporary string is not sent here; sendEncrypted() serializes the document again.
    // The actual wire newline is appended to the encrypted outer envelope in sendDoc().
    wire += '\n';
    if (sendEncrypted(doc)) {
        pending[(head+count)%32] = {sequence,millis()};
        ++count;

    }
}

// Called only after successful GCM authentication and replay checks in process().
// Check device/boot identity before releasing the oldest outstanding IMU slot.
void processAck(const String& line) {
    JsonDocument ack;
    auto error = deserializeJson(ack, line, DeserializationOption::NestingLimit(4));
    if (error || !ack.is<JsonObject>() ||
        !ack["v"].is<int>() || ack["v"].as<int>() != 1 ||
        !ack["type"].is<const char*>() || strcmp(ack["type"].as<const char*>(), "ACK") != 0 ||
        !ack["device_id"].is<uint32_t>() || ack["device_id"].as<uint32_t>() != DEVICE_ID ||
        !ack["boot_id"].is<const char*>() || strcmp(ack["boot_id"].as<const char*>(), bootId) != 0 ||
        !ack["ack_seq"].is<uint32_t>()) {
        Serial.println("[ACK REJECTED] Invalid format or wrong device/boot ID");
        return;
    }
    uint32_t sequence = ack["ack_seq"].as<uint32_t>();
    // TCP preserves order: accept exactly the oldest outstanding IMU ACK.
    if (!count || sequence != pending[head].seq) return;
    head=(head+1)%32; --count; ++ackReceived;
}


// A command uses the host sequence space (Ultra96, or PC in local mode).
// RESULT.seq uses the same ESP32 sequence counter as IMU, so IMU seq may skip.
// Dispatch authenticated application messages to ACK, configuration, file or MODIFY handling.
// MODIFY replies carry reply_to from the host command; their own seq belongs to this board.
void processFrame(const String& line) {
    JsonDocument command;
    auto error = deserializeJson(command, line, DeserializationOption::NestingLimit(4));
    if (error || !command.is<JsonObject>() || !command["type"].is<const char*>()) {
        Serial.println("[RX REJECTED] Invalid JSON/type");
        return;
    }
    const char* type = command["type"].as<const char*>();
    if (strcmp(type, "CALIBRATE") == 0) { processCalibration(command); return; }
    if (strcmp(type, "STATUS_ACK") == 0) return;
    if (strcmp(type, "ACK") == 0) {
        processAck(line);
        return;
    }
    if (strcmp(type,"CONFIG")==0 || strcmp(type,"FILE_CHUNK")==0) {
        processExtra(command); return;
    }
    if (strcmp(type, "MODIFY") != 0 ||
        !command["v"].is<int>() || command["v"].as<int>() != 1 ||
        !command["device_id"].is<uint32_t>() || command["device_id"].as<uint32_t>() != DEVICE_ID ||
        !command["boot_id"].is<const char*>() || strcmp(command["boot_id"].as<const char*>(), bootId) != 0 ||
        !command["seq"].is<uint32_t>() || !command["ts_ms"].is<uint32_t>() ||
        !command["imu"].is<JsonObject>()) {
        Serial.println("[MODIFY REJECTED] Invalid header or wrong device/boot ID");
        return;
    }
    // Reject missing, non-finite or out-of-bounds values before applying the dummy transformation.
    for (const char* axis : AXES) {
        if (!command["imu"][axis].is<float>()) {
            Serial.println("[MODIFY REJECTED] Missing/non-numeric IMU field");
            return;
        }
        float value = command["imu"][axis].as<float>();
        if (!isfinite(value) || fabsf(value) > 10000.0f) {
            Serial.println("[MODIFY REJECTED] Value outside demo bounds");
            return;
        }
    }
    uint32_t commandSeq = command["seq"].as<uint32_t>();
    float before = command["imu"]["ax"].as<float>();
    JsonDocument result;
    result["v"] = 1;
    result["type"] = "RESULT";
    result["device_id"] = DEVICE_ID;
    result["boot_id"] = bootId;
    result["seq"] = nextSeq++;
    result["ts_ms"] = millis();
    // Correlate this response with the host request independently of ongoing IMU transmissions.
    result["reply_to"] = commandSeq;
    for (const char* axis : AXES) result["imu"][axis] = command["imu"][axis].as<float>();
    result["imu"]["ax"] = before + 1.0f; // Visible dummy processing, not AI inference.
    String wire;
    serializeJson(result, wire);
    if (wire.length() > MAX_FRAME) {
        Serial.println("[RESULT ERROR] Frame too long");
        return;
    }
    // This temporary string is not sent here; sendEncrypted() serializes the document again.
    // The actual wire newline is appended to the encrypted outer envelope in sendDoc().
    wire += '\n';
    sendEncrypted(result);
    // RESULT is the completion reply. It does not change the outstanding IMU window.
}

// Represent binary ciphertext, tags and file chunks as hex inside JSON.
// Hex encoding is transport representation, not encryption.
String hexEncode(const uint8_t* p, size_t n) {
    const char* digits="0123456789abcdef";
    String s; s.reserve(n*2);
    for(size_t i=0;i<n;i++){s+=digits[p[i]>>4];s+=digits[p[i]&15];}
    return s;
}
int nibble(char c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;}
// Require exactly two valid lowercase hex characters per expected output byte.
bool hexDecode(const char* s,uint8_t* out,size_t n){
    if(!s||strlen(s)!=n*2)return false;
    for(size_t i=0;i<n;i++){int a=nibble(s[i*2]),b=nibble(s[i*2+1]);if(a<0||b<0)return false;out[i]=(a<<4)|b;}
    return true;
}
// CONFIG and FILE use the same authenticated framing, identity and sequence checks.
// CONFIG adjusts pacing/window; FILE_CHUNK returns one chunk from RAM.
// Both commands have already passed GCM and encrypted sequence verification.
void processExtra(JsonDocument& c) {
    if(!c["v"].is<int>()||c["v"].as<int>()!=1||!c["device_id"].is<uint32_t>()||c["device_id"].as<uint32_t>()!=DEVICE_ID||
       !c["boot_id"].is<const char*>()||strcmp(c["boot_id"],bootId)||!c["seq"].is<uint32_t>()) return;
    JsonDocument out;
    out["v"]=1; out["device_id"]=DEVICE_ID;out["boot_id"]=bootId;
    out["reply_to"]=c["seq"].as<uint32_t>();out["ts_ms"]=millis();
    if(strcmp(c["type"],"CONFIG")==0) {
        if(!c["interval_ms"].is<uint32_t>()||c["interval_ms"].as<uint32_t>()>1000||
           !c["window"].is<uint32_t>()||c["window"].as<uint32_t>()<1||c["window"].as<uint32_t>()>32)return;
        // Apply bounded settings immediately. A smaller window pauses new IMUs until enough ACKs arrive.
        intervalMs=c["interval_ms"];windowSize=c["window"];
        out["type"]="CONFIG_RESULT";out["interval_ms"]=intervalMs;out["window"]=windowSize;
        out["ack_received"]=ackReceived;
    } else {
        // One independent 1..128-byte RAM chunk. Offset/file_id are checked by host.
        const char* data=c["data"] | "";const char* id=c["file_id"] | "";
        uint8_t bytes[128];size_t n=strlen(data)/2;uint8_t idBytes[8];
        if(!n||n>128||!hexDecode(data,bytes,n)||!hexDecode(id,idBytes,8)||!c["offset"].is<uint32_t>())return;
        out["type"]="FILE_RESULT";out["file_id"]=id;out["offset"]=c["offset"].as<uint32_t>();
        // Echo the decoded bytes. The host checks offset/file ID, reconstructs the file and hashes it.
        // This board does not persist the file or resume an interrupted transfer.
        out["data"]=hexEncode(bytes,n);
    }
    out["seq"]=nextSeq++;sendEncrypted(out);
}
void closeSession(){resetConnection();}
// Serialize one outer JSON frame followed by newline for TCP message boundaries.
// This sends the public HELLO or an encrypted envelope, never the plaintext IMU document.
// Handle partial writes; the 3-second loop budget is checked between write calls.
bool sendDoc(JsonDocument& doc){
    String wire;serializeJson(doc,wire);wire+='\n';
    size_t sent=0;uint32_t start=millis();
    while(sent<wire.length()&&peer.connected()&&millis()-start<3000){
        size_t n=peer.write((const uint8_t*)wire.c_str()+sent,wire.length()-sent);sent+=n;if(!n)delay(1);
    }
    if(sent!=wire.length()){closeSession();return false;}return true;
}
// HMAC-SHA256 binds the PSK to protocol, device, both random challenges and direction.
// The pc argument is the host challenge: the host is Ultra96 in full pipeline mode.
// Fresh challenges provide new session keys; this PSK design does not provide forward secrecy.
bool derive(const String& pc,const char* direction,uint8_t* key){
    String context=String("secure-ultra-v1|device=")+String(DEVICE_ID)+"|"+boardChallenge+"|"+pc+"|"+direction;
    const mbedtls_md_info_t* md=mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    return md&&mbedtls_md_hmac(md,TEST_PSK,32,(const uint8_t*)context.c_str(),context.length(),key)==0;
}
// 96-bit GCM nonce: eight zero bytes followed by a 32-bit big-endian cipher counter.
// Nonce uniqueness depends on no counter reuse under the same directional session key.
void nonceFor(uint32_t seq,uint8_t* n){memset(n,0,12);n[8]=seq>>24;n[9]=seq>>16;n[10]=seq>>8;n[11]=seq;}
// Encrypt and authenticate a compact application JSON document with AES-256-GCM.
// Enforce the 512-byte plaintext limit and reconnect before the cipher counter wraps.
bool sendEncrypted(JsonDocument& doc){
    if(txSeq==UINT32_MAX){closeSession();return false;}
    String plain;serializeJson(doc,plain);
    if(plain.length()>512){closeSession();return false;}
    uint8_t nonce[12],ct[512],tag[16];uint32_t seq=txSeq++;nonceFor(seq,nonce);
    // AAD authenticates protocol/device/direction without encrypting those context bytes.
    // The host must build exactly the same AAD to verify the 16-byte tag.
    String aadText=String("secure-ultra-v1|device=")+String(DEVICE_ID)+"|e2p"; const char* aad=aadText.c_str();
    mbedtls_gcm_context g;mbedtls_gcm_init(&g);
    int rc=mbedtls_gcm_setkey(&g,MBEDTLS_CIPHER_ID_AES,txKey,256);
    if(!rc)rc=mbedtls_gcm_crypt_and_tag(&g,MBEDTLS_GCM_ENCRYPT,plain.length(),nonce,12,
        (const uint8_t*)aad,strlen(aad),(const uint8_t*)plain.c_str(),ct,16,tag);
    mbedtls_gcm_free(&g);
    if(rc){Serial.println("[CRYPTO ERROR] Encrypt failed");closeSession();return false;}
    // Outer seq is the nonce/replay counter; ct is ciphertext and tag authenticates it.
    // The application seq and IMU values remain inside the encrypted plaintext.
    JsonDocument out;out["seq"]=seq;out["ct"]=hexEncode(ct,plain.length());out["tag"]=hexEncode(tag,16);
    return sendDoc(out);
}
// Start a new session on every TCP connection, even when boot_id is unchanged.
// Reset cipher counters and pending ACKs before sending a fresh public challenge.
void beginSession(){
    ready=false;haveRx=false;txSeq=0;head=count=0;ackReceived=0;applied=authRejects=replayRejects=0;rxBuffer="";
    // Wi-Fi is active here, enabling ESP32's hardware RNG entropy source.
    uint8_t random[32];esp_fill_random(random,32);boardChallenge=hexEncode(random,32);
    JsonDocument hello;hello["type"]="HELLO";hello["device_id"]=DEVICE_ID;hello["challenge"]=boardChallenge;
    lastActivity=millis();sendDoc(hello);
}
// Process exactly one complete outer frame assembled by loop().
// State transition: awaiting HELLO -> keys derived -> authenticated application traffic.
void process(const String& line){
    JsonDocument outer;
    if(deserializeJson(outer,line,DeserializationOption::NestingLimit(3))||!outer.is<JsonObject>()){
        Serial.println("[FORMAT REJECT]");closeSession();return;
    }
    // HELLO is public and unauthenticated. ready means keys are derived, not peer authentication.
    // A valid incoming GCM tag is required before any host command or ACK is processed.
    if(!ready){
        uint8_t check[32];
        if(!outer["type"].is<const char*>()||strcmp(outer["type"],"HELLO")||
           !outer["challenge"].is<const char*>()||!hexDecode(outer["challenge"],check,32)){
            closeSession();return;
        }
        String pc=outer["challenge"].as<const char*>();
        if(!derive(pc,"e2p",txKey)||!derive(pc,"p2e",rxKey)){closeSession();return;}
        ready=true;Serial.println("[SESSION] Keys derived; awaiting authenticated command");sendCalibrationStatus();return;
    }
    if(!outer["seq"].is<uint32_t>()||!outer["ct"].is<const char*>()||!outer["tag"].is<const char*>()){
        closeSession();return;
    }
    uint32_t seq=outer["seq"].as<uint32_t>();
    const char* hex=outer["ct"].as<const char*>();size_t len=strlen(hex)/2;
    // Bound binary buffers before decoding. The extra plaintext byte holds the terminating NUL.
    uint8_t ct[512],plain[513],tag[16],nonce[12];
    if(!len||len>512||!hexDecode(hex,ct,len)||!hexDecode(outer["tag"],tag,16)){closeSession();return;}
    nonceFor(seq,nonce);String aadText=String("secure-ultra-v1|device=")+String(DEVICE_ID)+"|p2e"; const char* aad=aadText.c_str();
    mbedtls_gcm_context g;mbedtls_gcm_init(&g);
    int rc=mbedtls_gcm_setkey(&g,MBEDTLS_CIPHER_ID_AES,rxKey,256);
    // Verify the GCM tag while decrypting; do not dispatch plaintext on authentication failure.
    if(!rc)rc=mbedtls_gcm_auth_decrypt(&g,len,nonce,12,(const uint8_t*)aad,strlen(aad),tag,16,ct,plain);
    mbedtls_gcm_free(&g);
    if(rc){memset(plain,0,sizeof(plain));authRejects++;Serial.println("[AUTH REJECT] No command executed");return;}
    // Reject a repeated or older authenticated cipher sequence.
    // Only accepted authenticated traffic advances lastRx and the inactivity timestamp.
    if(haveRx&&seq<=lastRx){replayRejects++;Serial.println("[REPLAY REJECT] No command executed");return;}
    lastRx=seq;haveRx=true;lastActivity=millis();plain[len]=0;
    processFrame(String((const char*)plain));
}

// Initialize once per boot. bootId is a diagnostic reboot marker, not a cryptographic key.
void setup() {
    Serial.begin(115200);
    delay(1000);
    beginSensors();
    snprintf(bootId, sizeof(bootId), "%08lx", static_cast<unsigned long>(esp_random()));
    // reserve() preallocates storage, not a hard maximum; loop() enforces the 2048-byte receive limit.
    rxBuffer.reserve(MAX_FRAME);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false); // Throughput demo: trades power consumption for latency.
    WiFi.begin(SSID, PASSWORD);
    lastConnect = millis() - 2000;
    Serial.printf("[BOOT] device=%lu boot_id=%s\n", static_cast<unsigned long>(DEVICE_ID), bootId);
    Serial.println("[WIFI] Connecting...");
}

// Cooperatively service Wi-Fi recovery, TCP recovery, frame reception, ACK timeout and IMU pacing.
// These steps share the Arduino loop; network calls can block, so this is not a hard real-time scheduler.
void loop() {
    // Retry Wi-Fi every 10 seconds while disconnected; leave early until it reconnects.
    if (WiFi.status() != WL_CONNECTED) {
        if (peer.connected() || count || rxBuffer.length()) resetConnection();
        if (millis() - lastWifiRetry >= 10000) {
            lastWifiRetry = millis();
            Serial.println("[WIFI] Retrying...");
            WiFi.reconnect();
        }
        delay(10);
        return;
    }
    // Retry TCP every 2 seconds while Wi-Fi is available; begin a new key session after connection.
    if (!peer.connected()) {
        if (millis() - lastConnect >= 2000) {
            lastConnect = millis();
            resetConnection();
            Serial.printf("[TCP] Connecting to %s:%u\n", PC_IP, PORT);
            if (peer.connect(PC_IP, PORT)) {
                // Disable Nagle buffering to reduce delays for small IMU/ACK frames.
                peer.setNoDelay(true);
                Serial.println("[TCP] Connected");
                lastSend = millis() - 20;
                beginSession();
            }
        }
        delay(10);
        return;
    }
    // A frame may span several loop iterations; a read may contain several frames.
    // Bound work per loop to 2048 received bytes. Retain partial frames across iterations;
    // process each newline separately so both TCP fragmentation and combined frames are handled.
    for (size_t budget = 0; budget < 2048 && peer.available(); ++budget) {
        int value = peer.read();
        if (value < 0) break;
        char c = static_cast<char>(value);
        if (c == '\n') {
            if (rxBuffer.length()) process(rxBuffer);
            rxBuffer = "";
        } else {
            if (rxBuffer.length() >= 2048) {
                Serial.println("[RX ERROR] Frame too long");
                resetConnection();
                break;
            }
            rxBuffer += c;
        }
    }
    if (peer.connected() && ready && millis()-lastStatusSent >= 1000) sendCalibrationStatus();
    // If the oldest IMU lacks its matching ACK for five seconds, drop the session and reconnect.
    // TCP handles retransmission within a connection; this application does not replay abandoned IMUs.
    if (count && millis() - pending[head].at >= 5000) {
        Serial.println("[ACK TIMEOUT] No matching ACK in 5 seconds");
        resetConnection();
    }
    // Window avoids stop-and-wait RTT limit; bounded outstanding packets provide backpressure.
    if (peer.connected() && ready && count < windowSize && millis() - lastSend >= intervalMs) {
        sendImu();
    }
    // Close idle sessions after 15 seconds without accepted authenticated traffic.
    // This also bounds waiting after HELLO; reconnect is attempted on a subsequent loop iteration.
    if(peer.connected() && millis()-lastActivity>15000) resetConnection();
    delay(1);
}
