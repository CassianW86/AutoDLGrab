# AutoDL Grab

[简体中文](README.md) · English

## Additional features

The data disk filter and success alert sound were contributed by [KATare955](https://github.com/KATare955).

### 1. Minimum expandable data disk filter
A new "Min expandable data disk (GB)" field filters candidate hosts by remaining expandable data disk capacity.
Files: `market.py`, `app.py`, `static/index.html`, `static/panel.js`, `extension/translations.json`, `tests/test_grab.py`

### 2. Success alert sound
An alert sound plays from the backend when an instance is created (macOS prefers `asset/success.aiff` and falls back to the Glass chime; Windows uses the system beep). No need to keep the dashboard tab open.
Files: `engine.py`, `app.py`, `asset/success.aiff`

### 3. Compatibility fixes
- Playwright anti-automation flags for the dedicated browser, fixing repeated slider CAPTCHA failures on the AutoDL login page
- `Cache-Control: no-cache` for dashboard static assets, preventing stale pages after updates

AutoDL Grab is a local web app that waits for GPUs in the standard AutoDL marketplace. Choose the models you can use, an image, a GPU count per host, and an optional hourly budget. The app checks availability at your interval and stops after creating one pay-as-you-go instance.

For example, select RTX 4090 and RTX 5090 and request two GPUs. Either model can match if one host has two free cards of that model, supports the selected image, and meets your price limit. Hosts are checked in marketplace order. The order in which you select the models does not assign a preference.

## Run on Windows

1. Install Python 3.11 or later. The launcher validates `python` and `py`, then checks common Python, Miniconda, and Anaconda installation paths, skipping the Microsoft Store placeholder. Add other installation locations to `PATH`.
2. Download or clone the repository and double-click `start.bat` in its root directory. On first launch, the script creates a virtual environment and installs dependencies. The startup script prepares the browser environment.
3. The [local dashboard](http://127.0.0.1:8765/) opens in your browser. Keep the startup window running and the computer awake. Use the top-right 中文 / English selector to switch languages. The dashboard and extension connection page remember their language separately. AutoDL-provided model and image names keep their original text.

## Run on macOS

1. Install Python 3.11 or later. The launcher checks `python3`, `python`, and common Homebrew, Python.org and Conda installation paths.
2. Download or clone the project, open Terminal in the project directory, and run:

   ```bash
   bash start.command
   ```

   The script creates `.venv`, installs dependencies and Playwright Chromium, then opens the [local dashboard](http://127.0.0.1:8765/). The first launch needs internet access to download dependencies and the browser.
3. To launch by double-clicking in Finder, first run `chmod +x start.command` in the project directory, then double-click `start.command`. Keep Terminal running and your Mac awake.

macOS uses Playwright's downloaded Chromium for the dedicated login window; Edge is not required. For the extension connection, use a browser that supports Chromium extensions. Virtual environments cannot be copied between operating systems: move any Windows `.venv` elsewhere before running the Mac launcher. macOS startup support has not been tested on a Mac.

The dashboard and extension connection page both offer a 中文 / English selector and remember their language separately.

## Choose a browser connection

| Use case | How to connect | Available actions |
| --- | --- | --- |
| Rent automatically | Click **打开 AutoDL 登录** and sign in using the dedicated browser window. Keep it open. | Read the market, preview prices, and create one instance |
| Use an existing browser login | Connect the signed-in AutoDL tab through the bundled extension | Read models, images, stock, and quotes |

**Connect through the browser extension:** In a browser that supports Chromium extensions, open the extension manager, enable Developer mode, choose **Load unpacked**, and select the repository's `extension` folder. Return to the signed-in `www.autodl.com` tab and click **AutoDL Grab 本地连接** in the extension menu. When the connection page shows **已连接**, click **读取选项** in the dashboard to query the market. Keep both the AutoDL tab and the extension connection page open while using this connection.

## Preview and rent

1. Click **读取选项** to load model choices. Select one or more models and one base or private image. At least one selected model must support that image. The matcher also checks the individual host's architecture and driver compatibility.
2. The defaults are one GPU per host and 30 seconds between checks. Enter a positive integer to change the interval. The optional hourly ceiling applies to the entire instance. The app uses the undiscounted one-hour quote for the requested GPU count, so a host that meets the ceiling only after a member discount may be skipped. AutoDL determines the final charge.
3. Click **检查匹配** for a read-only preview of the matched GPU model, host, and hourly quote. **Clicking 开始抢卡 in dedicated-browser mode authorizes creation of a paid instance.** Open **我的实例** in AutoDL to use it, and shut it down when finished.
4. The first check runs immediately. Each subsequent wait starts after the preceding check finishes. Use **保存重试间隔** during a task to change the interval. Saving during a countdown restarts that countdown. Saving during a check applies to the next wait. Closing the dashboard tab leaves the local service running.

## Interruptions and order recovery

- Only one task runs at a time. If stock disappears during submission, the task waits and retries. If authentication expires or verification is required, complete it in the dedicated browser and click **继续任务**.
- Before submitting, the app saves a unique `grab-…` instance name in SQLite. If a response is lost or the service restarts during submission, it checks the instance list for that name. While the order is under review, it does not submit another order.
- Use **再次核对** to check again. Choose **手动核对** only after verifying in AutoDL that no instance was created. Stopping the task does not shut down or release a rented instance.
- The browser profile and task database live in `.local/`, which Git ignores. The server binds to loopback. AutoDL's web marketplace interfaces can change independently of this project.

## Development and tests

The backend uses FastAPI, Playwright, and SQLite. The dashboard uses vanilla JavaScript. Marketplace integration lives in `market.py`; task state and retry scheduling live in `engine.py`. After running the startup script, run the test suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/test_i18n.js
```

The suite uses a simulated market to test alternative models, single-host GPU counts, image and price constraints, interval changes, cancellation, uncertain order recovery, and the extension's read-only boundary.

On macOS, run Python tests with `.venv/bin/python -m unittest discover -s tests -v`. The bilingual test command is the same and requires Node.js to be installed separately.

## License

Released under the [MIT License](LICENSE). Use, modification, redistribution, and commercial use are permitted. Include the copyright and license notices when distributing copies or substantial portions.
