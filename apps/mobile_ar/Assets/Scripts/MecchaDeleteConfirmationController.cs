using UnityEngine;

public class MecchaDeleteConfirmationController :
    MonoBehaviour
{
    [Header("References")]

    [SerializeField]
    private SpawnMeccha
        spawnMeccha;

    [SerializeField]
    private GameObject
        confirmationPanel;

    [Header("Optional")]

    [Tooltip(
        "Close the Meccha fine-tune panel while " +
        "the delete confirmation is open."
    )]
    [SerializeField]
    private bool closeFineTunePanelWhenOpened =
        true;

    private void Awake()
    {
        if (spawnMeccha == null)
        {
            spawnMeccha =
                FindFirstObjectByType<
                    SpawnMeccha
                >();
        }

        if (confirmationPanel != null)
        {
            confirmationPanel.SetActive(
                false
            );
        }
    }

    // DELETE BUTTON

    public void OpenDeleteConfirmation()
    {
        if(
            spawnMeccha == null || 
            !spawnMeccha.HasSpawnedMecchas()
        )
        {
            return;
        }
        
        if (confirmationPanel == null)
        {
            Debug.LogWarning(
                "[DeleteConfirmation] " +
                "Confirmation Panel is not assigned."
            );

            return;
        }

        if (
            closeFineTunePanelWhenOpened &&
            spawnMeccha != null &&
            spawnMeccha.fineTuneController != null
        )
        {
            spawnMeccha
                .fineTuneController
                .CloseFineTunePanel();
        }

        confirmationPanel.SetActive(
            true
        );
    }

    // YES BUTTON

    public void ConfirmDelete()
    {
        // Close popup first.
        if (confirmationPanel != null)
        {
            confirmationPanel.SetActive(
                false
            );
        }

        if (spawnMeccha == null)
        {
            Debug.LogWarning(
                "[DeleteConfirmation] " +
                "SpawnMeccha is not assigned."
            );

            return;
        }

        // Use the existing deletion logic.
        spawnMeccha
            .DeleteMostRecentMeccha();
    }

    // NO BUTTON

    public void CancelDelete()
    {
        if (confirmationPanel != null)
        {
            confirmationPanel.SetActive(
                false
            );
        }
    }

    public void CloseConfirmationPanel()
    {
        if (confirmationPanel != null)
        {
            confirmationPanel.SetActive(
                false
            );
        }
    }
}