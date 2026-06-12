"""
OBS URL/API Source changer via WebSocket (TCP)
Requires: pip install obs-websocket-py

Usage:
    python obs_change_source.py
    python obs_change_source.py --host localhost --port 4455 --password yourpassword --source "My Source" --url "https://example.com/api"
"""

import argparse
import sys
import time
import json

try:
    import obswebsocket
    from obswebsocket import obsws, requests as obsrequests
except ImportError:
    print("Error: obs-websocket-py is not installed.")
    print("Install it with: pip install obs-websocket-py")
    sys.exit(1)


def change_source_url(host: str, port: int, password: str, source_name: str, new_url: str) -> bool:
    ws = obsws(host, port, password)

    try:
        print(f"Connecting to OBS at {host}:{port} ...")
        ws.connect()
        print("Connected.")

        # Step 1: Get current settings so we can patch request_data
        response = ws.call(obsrequests.GetInputSettings(inputName=source_name))
        current_settings = response.getInputSettings()

        # Step 2: Parse the nested request_data JSON string and update its URL
        request_data = json.loads(current_settings["request_data"])
        request_data["url"] = new_url
        updated_request_data = json.dumps(request_data, separators=(',', ':'))  # compact, no spaces

        # Step 3: Write both URLs back
        print(f"Updating source '{source_name}' -> {new_url}")
        ws.call(obsrequests.SetInputSettings(
            inputName=source_name,
            inputSettings={
                "url": new_url,
                "request_data": updated_request_data,
            }
        ))
        print("Settings written.")

        # Step 2: Find which scene item ID this source has (needed to toggle it)
        # We check all scenes to find the source
        scenes_response = ws.call(obsrequests.GetSceneList())
        scenes = scenes_response.getScenes()

        scene_name = None
        scene_item_id = None

        for scene in scenes:
            items_response = ws.call(obsrequests.GetSceneItemList(sceneName=scene["sceneName"]))
            for item in items_response.getSceneItems():
                if item["sourceName"] == source_name:
                    scene_name = scene["sceneName"]
                    scene_item_id = item["sceneItemId"]
                    break
            if scene_item_id is not None:
                break

        if scene_item_id is None:
            # Source exists but isn't placed in any scene — still try a settings re-push
            print("Warning: source not found in any scene, cannot toggle. Trying double set-settings workaround...")
            ws.call(obsrequests.SetInputSettings(
                inputName=source_name,
                inputSettings={"url": ""}
            ))
            time.sleep(0.1)
            ws.call(obsrequests.SetInputSettings(
                inputName=source_name,
                inputSettings={"url": new_url}
            ))
            print("Done (no scene item toggle available).")
            return True

        # Step 3: Disable the source (makes the plugin drop its state)
        print(f"Toggling source off (scene: '{scene_name}', item id: {scene_item_id}) ...")
        ws.call(obsrequests.SetSceneItemEnabled(
            sceneName=scene_name,
            sceneItemId=scene_item_id,
            sceneItemEnabled=False
        ))

        time.sleep(0.3)  # Give the plugin a moment to release

        # Step 4: Re-enable the source (plugin reinitialises, picks up the new URL)
        print("Toggling source back on ...")
        ws.call(obsrequests.SetSceneItemEnabled(
            sceneName=scene_name,
            sceneItemId=scene_item_id,
            sceneItemEnabled=True
        ))

        print("Source URL updated and reloaded successfully.")
        return True

    except obswebsocket.exceptions.ConnectionFailure:
        print(f"Error: Could not connect to OBS at {host}:{port}.")
        print("Make sure OBS is running and WebSocket server is enabled:")
        print("  Tools > WebSocket Server Settings > Enable WebSocket server")
        return False

    except Exception as e:
        print(f"Error: {e}")
        return False

    finally:
        try:
            ws.disconnect()
            print("Disconnected from OBS.")
        except Exception:
            pass


def interactive_mode():
    print("=== OBS URL/API Source Changer ===\n")
    host     = input("OBS host     [localhost]: ").strip() or "localhost"
    port_str = input("OBS port     [4455]:      ").strip() or "4455"
    password = input("Password     (leave blank if none): ").strip()
    source   = input("Source name: ").strip()
    new_url  = input("New URL:     ").strip()

    try:
        port = int(port_str)
    except ValueError:
        print("Invalid port number.")
        sys.exit(1)

    if not source:
        print("Source name cannot be empty.")
        sys.exit(1)
    if not new_url:
        print("URL cannot be empty.")
        sys.exit(1)

    success = change_source_url(host, port, password, source, new_url)
    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(
        description="Change the URL of an OBS URL/API Source via WebSocket."
    )
    parser.add_argument("--host",     default=None, help="OBS WebSocket host (default: localhost)")
    parser.add_argument("--port",     type=int, default=None, help="OBS WebSocket port (default: 4455)")
    parser.add_argument("--password", default=None, help="OBS WebSocket password")
    parser.add_argument("--source",   default=None, help="Exact source name in OBS")
    parser.add_argument("--url",      default=None, help="New URL to set on the source")

    args = parser.parse_args()

    if not all([args.host, args.port, args.source, args.url]):
        interactive_mode()
    else:
        password = args.password or ""
        success = change_source_url(args.host, args.port, password, args.source, args.url)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()