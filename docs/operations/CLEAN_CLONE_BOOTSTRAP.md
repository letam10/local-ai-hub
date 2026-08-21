# Clean-clone bootstrap (V7)

1. Clone the repository and enter the checkout.
2. Run `scripts/setup_local_ai_hub.ps1` without `-Apply` to inspect Core,
   WebView2 and the production catalog.
3. Review the dry-run plan. It contains no model URL, command, credential or
   machine path.
4. Run `scripts/setup_local_ai_hub.ps1 -Apply` to create only absent Core
   folders/configuration and the verified managed shortcut.
5. Double-click **Local AI Hub**. The desktop shell starts the same loopback
   API and workspace even when Models/Environments/runtime are empty.

The setup command never downloads models or installs optional environments.
Use Models & Storage / Components to review a separate dependency plan. A
missing or unauthenticated source stays visible with a truthful action.
