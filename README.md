# Remote Mouse

Use your phone (iPhone or Android) as an air mouse and touchpad for your Mac. There's nothing to install on the phone; it runs in the browser.

## Run

```sh
./start.sh
```

Scan the QR code with your phone (it must be on the same Wi-Fi), accept the certificate warning, and tap **Start**.

- **Certificate warning:** phones only give motion sensors to `https://` pages, so the server uses a self-signed certificate. In Safari tap *Show Details → visit this website*. In Chrome tap *Advanced → Proceed*.
- **Accessibility:** cursor movement works right away. For clicks, drags and scrolling, enable your terminal app under *System Settings → Privacy & Security → Accessibility*, then restart.

## Controls

| Action | How |
|---|---|
| Point with the phone | Tap **Air** to turn it on, then aim the phone like a remote (flat or upright, portrait) |
| Reposition your hand | Turn **Air** off, move your hand, turn it back on |
| Move / click | One-finger drag / tap on the pad |
| Right click | Two-finger tap, or the **Right** button |
| Scroll | Two-finger drag |
| Drag | Hold **Left** while moving |

If the cursor moves the wrong way, use the invert switches in Settings (⚙︎).

`./start.sh --reset-token` invalidates the old phone link. `--port` changes the port (default 8443).
