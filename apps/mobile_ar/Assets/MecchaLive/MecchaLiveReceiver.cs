using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

[Serializable]
public class MecchaLivePacket
{
    public int v, device_id, class_id, generation, seq, input_epoch;
    public long event_id, sample_seq, ts_ms;
    public string type, session, boot_id, gesture, ai_fault;
    public bool online, ready, fresh, ai_valid, simulated, flex_invalid;
    public float ax, ay, az, gx, gy, gz;
}

public class MecchaLiveReceiver : MonoBehaviour
{
    [Serializable] class Config { public string host, token, pin; public int port = 5503; }
    class Delivery
    {
        public MecchaLivePacket packet;
        public int run;
        public bool disconnected;
        public long queuedAt = System.Diagnostics.Stopwatch.GetTimestamp();
        public TaskCompletionSource<bool> done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    }
    public MecchaLiveController controller;
    [Range(1, 2)] public int deviceId = 1;
    public string laptopIPOverride = "";
    public int portOverride;
    public string status = "Stopped";
    int runGeneration;
    [Serializable] class Hello { public int v = 2; public string type = "VISUALIZER_HELLO"; public int device_id; public string token; }
    readonly ConcurrentQueue<Delivery> incoming = new ConcurrentQueue<Delivery>();
    readonly ConcurrentQueue<string> notices = new ConcurrentQueue<string>();
    CancellationTokenSource stop;
    Config config;
    string expectedPin;

    void OnEnable()
    {
        try
        {
            if (controller == null) throw new Exception("Assign MecchaLiveController");
            TextAsset asset = Resources.Load<TextAsset>("phone_config");
            if (asset == null) throw new Exception("Missing Resources/phone_config.json");
            config = JsonUtility.FromJson<Config>(asset.text);
            if (!string.IsNullOrWhiteSpace(laptopIPOverride)) config.host = laptopIPOverride.Trim();
            if (string.IsNullOrWhiteSpace(config.host) || string.IsNullOrEmpty(config.token) ||
                config.pin == null || config.pin.Length != 64 || config.port < 1 || config.port > 65535)
                throw new Exception("Invalid phone_config");
            if (portOverride != 0) config.port = portOverride;
            if (config.port < 1 || config.port > 65535) throw new Exception("Invalid port override");
            expectedPin = config.pin.ToLowerInvariant();
            stop = new CancellationTokenSource();
            _ = Run(stop.Token, deviceId, ++runGeneration);
        }
        catch (Exception error) { status = error.Message; Debug.LogError(status); }
    }

    bool VerifyCertificate(object sender, X509Certificate certificate, X509Chain chain, SslPolicyErrors errors)
    {
        if (certificate == null) return false;
        using (var cert = new X509Certificate2(certificate))
        using (var sha = SHA256.Create())
        {
            string pin = BitConverter.ToString(sha.ComputeHash(cert.RawData)).Replace("-", "").ToLowerInvariant();
            DateTime now = DateTime.UtcNow;
            return pin == expectedPin && now >= cert.NotBefore.ToUniversalTime() && now <= cert.NotAfter.ToUniversalTime();
        }
    }

    async Task Run(CancellationToken token, int subscribedDevice, int run)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                using (var tcp = new TcpClient())
                using (token.Register(() => tcp.Close()))
                {
                    var connect = tcp.ConnectAsync(config.host, config.port);
                    if (await Task.WhenAny(connect, Task.Delay(8000, token)) != connect) throw new IOException("Connect timeout");
                    await connect;
                    tcp.NoDelay = true;
                    using (var tls = new SslStream(tcp.GetStream(), false, VerifyCertificate))
                    {
                        var auth = tls.AuthenticateAsClientAsync("ultra-visualizer", null, System.Security.Authentication.SslProtocols.Tls12, false);
                        if (await Task.WhenAny(auth, Task.Delay(8000, token)) != auth) throw new IOException("TLS timeout");
                        await auth;
                        using (var reader = new StreamReader(tls, Encoding.UTF8))
                        using (var writer = new StreamWriter(tls, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" })
                        {
                            await writer.WriteLineAsync(JsonUtility.ToJson(new Hello { device_id = subscribedDevice, token = config.token }));
                            string session = null;
                            int expectedSeq = 0;
                            notices.Enqueue("Connected: REAL FPGA");
                            while (!token.IsCancellationRequested)
                            {
                                var read = ReadBoundedLine(reader);
                                if (await Task.WhenAny(read, Task.Delay(2000, token)) != read) throw new IOException("Stream timeout");
                                string line = await read;
                                if (line == null || line.Length > 4096) throw new IOException("Closed/oversize frame");
                                MecchaLivePacket packet = JsonUtility.FromJson<MecchaLivePacket>(line);
                                if (packet == null || packet.v != 2 || packet.type != "VISUALIZER_STATE" || packet.simulated ||
                                    packet.device_id != subscribedDevice || string.IsNullOrEmpty(packet.session) || packet.seq != expectedSeq)
                                    throw new IOException("Invalid visualizer frame");
                                if (session == null) session = packet.session;
                                if (session != packet.session || (packet.ai_valid && (packet.class_id < 0 || packet.class_id >= MecchaGestureGate.Labels.Length)))
                                    throw new IOException("Invalid session/class");
                                if (packet.ai_valid && packet.gesture != MecchaGestureGate.Labels[packet.class_id])
                                    throw new IOException("Gesture label/index mismatch");
                                var delivery = new Delivery { packet = packet, run = run };
                                incoming.Enqueue(delivery);
                                if (await Task.WhenAny(delivery.done.Task, Task.Delay(1000, token)) != delivery.done.Task)
                                {
                                    delivery.done.TrySetCanceled();
                                    throw new IOException("Unity main thread timeout");
                                }
                                await delivery.done.Task;
                                await writer.WriteLineAsync("{\"v\":2,\"type\":\"UI_ACK\",\"ok\":true,\"session\":\"" + session + "\",\"seq\":" + expectedSeq + "}");
                                expectedSeq++;
                            }
                        }
                    }
                }
            }
            catch (Exception error)
            {
                if (run == runGeneration && !token.IsCancellationRequested)
                {
                    notices.Enqueue("Disconnected: " + error.Message);
                    incoming.Enqueue(new Delivery { run = run, disconnected = true });
                }
            }
            if (!token.IsCancellationRequested)
                try { await Task.Delay(1500, token); } catch (OperationCanceledException) { }
        }
    }

    // Bound memory before a newline arrives (ReadLineAsync alone is unbounded).
    static async Task<string> ReadBoundedLine(StreamReader reader)
    {
        var line = new StringBuilder();
        var one = new char[1];
        while (line.Length <= 4096)
        {
            if (await reader.ReadAsync(one, 0, 1) == 0) throw new IOException("Stream closed");
            if (one[0] == '\n') return line.ToString();
            if (one[0] != '\r') line.Append(one[0]);
        }
        throw new IOException("Frame exceeds 4096 characters");
    }

    void Update()
    {
        while (notices.TryDequeue(out string notice)) status = notice;
        while (incoming.TryDequeue(out Delivery delivery))
        {
            if (delivery.run != runGeneration || delivery.done.Task.IsCompleted) continue;
            if (delivery.disconnected) { controller?.StopInput(); continue; }
            double queuedSeconds = (System.Diagnostics.Stopwatch.GetTimestamp() - delivery.queuedAt) / (double)System.Diagnostics.Stopwatch.Frequency;
            if (queuedSeconds > 0.5)
            {
                controller?.StopInput();
                delivery.done.TrySetException(new IOException("Expired Unity delivery"));
                continue;
            }
            try
            {
                if (controller == null || !controller.isActiveAndEnabled) throw new Exception("Gesture controller inactive");
                controller.Accept(delivery.packet);
                delivery.done.TrySetResult(true);
            }
            catch (Exception error) { delivery.done.TrySetException(error); }
        }
    }

    void OnDisable()
    {
        runGeneration++;
        stop?.Cancel();
        stop?.Dispose();
        stop = null;
        while (incoming.TryDequeue(out Delivery delivery)) delivery.done.TrySetCanceled();
        if (controller != null) controller.StopInput();
    }
}
