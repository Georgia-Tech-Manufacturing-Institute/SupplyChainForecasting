
# System basics

Major elements:
- **Forecast App**: a Flask-based web interface (`app/ui/app.py`) that runs on your own computer and opens in a web browser at http://127.0.0.1:5000. 
- **Automation flow** (`app/routine.py`): the weekly load -> retrain -> predict routine. It can be started from the Runs page in the app, or from the command line / Windows Task Scheduler.
- **Reporting** (`app/reporting/`): order-accuracy (error) reports, data-coverage reports, and model holdout reports.
- **Per-plant databases**: every plant has its own SQLite database at `data/processed/<plant>.db`, built from raw weekly files stored under `data/<Plant>/<data type>/`.

Where things live on disk (all relative to the project folder):

| Folder | What's in it | Stored in Git? |
|---|---|---|
| `app/` | All of the code | Yes |
| `data/<Plant>/` | Raw weekly files (Waterfall, Consumption, Cost) | **No** |
| `data/processed/` | One SQLite database per plant | **No** |
| `data/incoming/` | Default folder the automation checks for new extracts | **No** |
| `models/saved_models/` | Trained models (`.pkl`) | **No** |
| `reports/` | Weekly outputs, e.g. `reports/2026wk41/predictions_*.xlsx` | **No** (`.xlsx` is ignored) |
| `logs/routine_runs/` | Run history shown on the Runs page | **No** (only a couple of old examples are) |

The "No" rows matter a lot when moving the tool to another computer; see [Moving devices](#moving-devices).

# Basic user flows
- **Automated workflow**: how it works when it works correctly.
- **Manual reporting**: feedback on the data and on model performance.
- **Filling missing week data**: loading weeks by hand when the automation missed them.

## Automated Workflow
The fundamental workflow for the tool does not involve any manual steps once it is set up:

1. The SCMDW folder updates with new data on Tuesdays. This includes two files per plant: `ictrans` and `custdemand`. `ictrans` gives all transactions recorded within the past 8 days, while `custdemand` gives the customer waterfall. 
2. The routine in `app/routine.py` (a Python script, not a bash script) looks in that folder for files named `[plantkey]_ictrans.txt` and `[plantkey]_custdemand.txt`, where the plant key comes from the `plant_key` dictionary in `app/prefixe.py` (e.g. Arlington -> `AT_ictrans.txt`, `AT_custdemand.txt`).
3. For each run, the routine:
   1. Writes an error report into `reports/<year>wk<week>/`.
   2. Loads whichever of the two files it finds into the plant's database, then **moves** each file into `data/<plant>/<data type>/<year>wk<week>.txt` as an archive. If neither file is found, it stops here and logs the run as `no_new_data`.
   3. Retrains the model (one week held out), using the configuration of the best previously saved model for that plant.
   4. Saves the model as `models/saved_models/<plantkey>_<year>wk<week>_<timestamp>.pkl`.
   5. Makes predictions 4 weeks ahead and saves them to `reports/<year>wk<week>/predictions_<model name>.xlsx`.
4. Every step is recorded as passed/failed/skipped in `logs/routine_runs/`, which the **Runs** page displays.

To run it by hand from a Command Prompt in the project folder (with the virtual environment activated, see below):
```
python -m app.routine "<folder containing the extracts>" Arlington
```
Note: use `python -m app.routine`, not `python app\routine.py`; the second form can't find the `app` package.

When started from the **Runs** page instead, the incoming folder has to be inside the project's `data/` folder (by default `data/incoming/`), because the app only lets you browse there.

TODO: how is the weekly run actually triggered on the production machine today (Task Scheduler, someone clicking the Runs page, something else)? See [Step 9](#step-9-optional-schedule-the-weekly-automation) under Moving devices for a Task Scheduler setup.


## Manual reporting

The reporting module exists to provide feedback on the tool itself and model performance. Reports are run from the **Reports** page; pick a plant, an optional week range, an optional part-number prefix, and optionally a folder to save the output to. The following operations are supported:

- **Order accuracy / ordering history report** (`error_report`): compares historic customer forecasts (waterfall) against what was actually ordered, broken out by how far ahead the forecast was made (lookahead). Use "max lookahead" to limit how far out to look.
- **Data coverage report** (`coverage_report`): shows which weeks and parts are present in each table, to spot missing weeks.
- **Model performance report** (`holdout_report`, on the **Evaluate** page): for a given trained model and test dataset, provides diagnostics about past model performance. 

## Filling missing week data
Based on the data mosaic shown on the Load page, it may be evident that there is missing info. To manually load data outside of the automation pipeline:

1. Get the file(s) for the missing week(s). 
2. On the **Load** page, select the plant under "Plant Source", choose the "Data Type", and upload the file(s).
3. Re-check the coverage heatmap on the Load page to confirm the gap is filled.
4. Retrain (Retrain page, or the next automation run) so the model sees the new data.

Note that because the week is the smallest unit of time, a week that is only partly loaded is treated as incomplete.

# Advanced user flows
This is stuff that should be left to the professionals (i.e. someone who knows how to roll back a version, at least; see [Rolling back to an older version](#rolling-back-to-an-older-version)). There are a lot of opportunities for things to go wrong, so make a copy of the `data/` and `models/` folders before starting.

## Change the automation path 

`app/routine.py` has no saved setting for where it looks. The incoming folder is passed in each time the routine starts, so you change it wherever the routine is started from. Whichever you use, the new folder must hold the extracts **directly** (e.g. `AT_ictrans.txt`, `AT_custdemand.txt`), not in subfolders.

- **Scheduled task (production):** open **Task Scheduler**, find the plant's task (e.g. `Forecast Tool - Arlington weekly`), right-click -> **Properties** -> **Actions** tab -> select the action -> **Edit...**. Change the quoted folder in **Add arguments**, e.g. `-m app.routine "\\newserver\share\SCDW" Arlington`. Click **OK** twice. There is one task per plant, so repeat for each. Use the full network path, not a mapped drive letter (see [Step 9](#step-9-optional-schedule-the-weekly-automation)).
- **Command line:** the folder is the first argument:
  ```
  python -m app.routine "<new folder>" Arlington
  ```
- **Runs page:** pick the folder in the incoming-folder box before starting a run. The page can only browse inside the project's `data/` folder. To change the folder it starts in, edit the `'incoming'` entry in `dirs` in `app/prefixe.py` and restart the app. That entry must still point somewhere inside `data/`, or the Runs page will reject it. A network share can't be used here; use a scheduled task or the command line for those.

Processed files are moved to `data/<plant>/<data type>/` by default. To archive them somewhere else, add a third argument: `python -m app.routine "<incoming folder>" Arlington "<archive folder>"`.

After changing the path, test it: put the week's extracts in the new folder (or wait for the next drop), run the routine once, and check that the **Runs** page shows the load steps as passed rather than skipped. Don't just look for `no_new_data`: that only means the files weren't found.

## New plant
Needed: historical data in a known form. Form is either the concatenated diamond reports or the automated warehouse format; the latter is preferred.

1. Register the plant source. Open `app/prefixe.py`:
   - Add the name of the plant to the `_BASE_PLANT_SOURCES` list.
   - Double check that there is an existing entry for the plant in `plant_key` (lowercase name -> key). The automation needs this key to find the plant's `[key]_ictrans.txt` / `[key]_custdemand.txt` files.
2. Prepare the raw historical files for the plant, one file per week, named `YYYYwkWW.prn` or `.txt` (e.g. `2026wk01.prn`), for each data type you have (Waterfall, Consumption, Cost).
3. On the **Load** page, select the new plant under "Plant Source", choose the "Data Type", and upload the files (individually or as a folder). Files are copied to `data/<plant>/<data type>/` and parsed into that plant's database.
4. Repeat step 3 for each data type / batch of weeks you need to backfill.
5. Confirm the load on the Home page's Plant Sources table (weeks/parts counts) or the Load page's coverage heatmap.
6. Train a first model for the plant on the Retrain page so the automation has a configuration to start from.


## Deleting a plant
Given a plant "ExamplePlant", say we want to remove it and all its data. There is currently no UI action for this — it must be done by hand:
1. Remove raw data: delete the `data/ExamplePlant/` directory (the raw `.prn`/`.txt` files uploaded via the Load page).
2. Remove the processed database: delete `data/processed/exampleplant.db` (note the plant's database file is always named in lowercase, regardless of how the plant name is cased elsewhere).
3. Remove it from the plant list:
   - If it was added via the Home page's "Add Plant" form, remove its entry from `data/custom_plants.json`.
   - If it was added by hand, remove it from the `_BASE_PLANT_SOURCES` list in `app/prefixe.py`.
4. Optionally, delete any saved models trained on that plant under `models/saved_models/` (check each bundle's `plant` field, e.g. via the Models page).
5. Restart the app so the in-memory `PLANT_SOURCES` list picks up the removal.

## Feature Development

Model features are elaborated in `app/models/features.py`. There are 4 types of features: waterfall, consumption, accuracy, and part.

The BIGGEST concern when adding new features is ENSURING that future information is NOT included. The input to both model training and prediction is a row representing a future order date (`orderidx`), made on a date (`predidx`). Each row should contain features (columns) ONLY based on information available up to the prediction date. Incorporating future information will often improve performance, but requires information that isn't actually available for real future projections!

Feature engineering is a tantalizing method of model improvement. However, there are a lot of pitfalls within this dataset in particular.
- Seeing into the future: This is time series 101, but just to be abundantly clear: features should NEVER use information not available at the time of row creation. This is tricky with these datasets, because when the consumption and prediction data are merged, you can accidentally look into the future if you're not careful.
  - For example: if data is sorted according to `orderidx` rather than `predidx`, then a "prior" row might already have been ordered, giving unrealistic insight.
- Lag != prior week. This is one I have to catch myself doing; because customer demand is not always present for every week, sometimes the "last" value is from many weeks ago. At the time of the last week, the lagging value IS the last known prediction, but it was not forecast in the prior week.
- Holdout noise: a single holdout split can swing the error more than a good feature does. Compare features over several rolling origins before concluding a feature helps.

Opportunities for new features (performed by an intern or others):
- Exogenous features: 
   - Weather forecasts for plants
   - Gas prices 
   - OEM info: production schedules, holiday/shutdown calendars


## Moving devices

This section covers moving the Forecast Tool to a new Windows computer (or setting it up on one for the first time). It is written for someone who has never used Git. Follow the steps in order and don't skip any.

### What moves how

The code is stored on GitHub at:

https://github.com/Georgia-Tech-Manufacturing-Institute/SupplyChainForecasting

Git (the tool that downloads the code from GitHub) **only carries the code**. The data, the trained models, the reports, and the run history are deliberately left out of Git (they're large, and the data is company information). Those have to be copied over by hand from the old computer. If you only do the Git part, the app will start but will have no plants' data and no models.

| What | How it gets to the new computer |
|---|---|
| Code (`app/`, `Dockerfile`, `README.md`, etc.) | Git (Step 3) |
| `requirements.txt` (the list of Python packages) | Git (Step 3) |
| `data/` folder (raw files + databases) | Copy by hand (Step 6) |
| `models/` folder (trained models) | Copy or run optimization |
| `reports/` folder (past outputs) | Copy by hand (Step 6), optional |
| `logs/` folder (Runs page history) | Copy by hand (Step 6), optional |
| `.env` file | Copy by hand (Step 6), only needed for Docker |
| `.venv` folder (installed Python packages) | **Do not copy.** Recreate it on the new computer (Step 5). |

Throughout these steps:
- **Command Prompt** means the Windows program called "Command Prompt" (not PowerShell). Open it by clicking the Start menu, typing `cmd`, and pressing Enter. It's a black window where you type commands.
- When a step says "run", type the command exactly as shown and press **Enter**. Wait for it to finish (you'll get a new blinking line ending in `>`) before typing the next one.
- `<like this>` means "replace this, including the angle brackets, with your own value".
- You can paste into Command Prompt with **Ctrl+V** or a right-click.

### Step 0: Before you start

You will need:
- [ ] A GitHub account that has access to the `Georgia-Tech-Manufacturing-Institute/SupplyChainForecasting` repository. Contact Nikki or others to share to new users. 
- [ ] Permission to install software on the new computer (or someone from IT who can).
- [ ] Access to the old computer, or a backup of its `data/` and `models/` folders (a USB drive or shared network drive large enough to hold them).
- [ ] The path to the automated data drop folder, if the new computer will run the weekly automation. 

On the **old** computer, close the app first (close its black window, or press Ctrl+C in it), so the databases aren't being written to while you copy them.

### Step 1: Install Python 3.12

1. Go to https://www.python.org/downloads/windows/ and download the latest **Python 3.12** "Windows installer (64-bit)". The tool was developed on Python 3.12; newer versions may work but haven't been tested.
2. Double-click the downloaded file.
3. **On the first screen, tick the box "Add python.exe to PATH".** This is the most common thing people miss, and the app won't start without it.
4. Click **Install Now**. Click **Yes** if Windows asks for permission.
5. When it says "Setup was successful", click **Close**.
6. Check it worked: open a **new** Command Prompt and run:
   ```
   python --version
   ```
   It should print `Python 3.12.<something>`.
   - If it says `'python' is not recognized...`, re-run the installer, choose **Modify**/**Repair**, and make sure "Add python.exe to PATH" is ticked.
   - If it opens the Microsoft Store instead, open Start -> type `Manage app execution aliases` -> turn **off** both "App Installer python.exe" and "python3.exe", then open a new Command Prompt and try again.

### Step 2: Install Git

1. Go to https://git-scm.com/download/win and download the "64-bit Git for Windows Setup".
2. Double-click the downloaded file and click **Yes** if Windows asks for permission.
3. Click **Next** on every screen, keeping the default options, then **Install**, then **Finish**.
4. Check it worked: open a **new** Command Prompt and run:
   ```
   git --version
   ```
   It should print `git version 2.<something>`.
5. Tell Git your name and email (these get attached to any changes you save; use your work email):
   ```
   git config --global user.name "<Your Name>"
   git config --global user.email "<you@example.com>"
   ```

### Step 3: Download the code (clone the repository)

"Cloning" downloads the code and keeps a link to GitHub so you can get updates later.

1. Decide where the tool will live. These instructions use `C:\ForecastTool`. Avoid putting it inside a OneDrive-synced folder (like `Documents` or `Desktop` on many work laptops): OneDrive syncing can lock the database files while the app is using them.
2. In Command Prompt, run these one at a time:
   ```
   mkdir C:\ForecastTool
   cd /d C:\ForecastTool
   git clone https://github.com/Georgia-Tech-Manufacturing-Institute/SupplyChainForecasting.git
   ```
3. The first time, a window may pop up asking you to sign in to GitHub. Choose **Sign in with your browser**, log in, and click **Authorize**. Then go back to Command Prompt.
   - If you get `Repository not found` or `Permission denied`, your GitHub account hasn't been given access yet (see Step 0).
4. When it finishes, the code is in `C:\ForecastTool\SupplyChainForecasting`. Move into that folder:
   ```
   cd /d C:\ForecastTool\SupplyChainForecasting
   ```
   **Every remaining command in this guide is run from this folder.** If you open a new Command Prompt later, run that `cd /d ...` line first.

### Step 4: Check that `requirements.txt` came down

`requirements.txt` lists the Python packages the tool needs, with exact versions. It comes with the code. Check it's there:
```
dir requirements.txt
```
If it says `File Not Found`, the clone in Step 3 didn't finish, or you're in the wrong folder. Re-check Step 3 before going on.

Don't change the version numbers in it. The `scikit-learn` and `joblib` versions must match the ones the saved models were made with, or the app won't be able to load the models you copy over in Step 6. If you do upgrade them, retrain the models afterwards.

### Step 5: Create the Python environment and install packages

A "virtual environment" is a private copy of Python just for this tool, so its packages don't clash with anything else on the computer.

1. Create it (this makes a `.venv` folder inside the project):
   ```
   python -m venv .venv
   ```
2. Turn it on ("activate" it):
   ```
   .venv\Scripts\activate.bat
   ```
   The start of your Command Prompt line should now say `(.venv)`. You need to do this every time you open a new Command Prompt to work with the tool. (The launcher in Step 8 does it for you.)
3. Install the packages:
   ```
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
   This takes a few minutes. It's done when you get the `(.venv) C:\ForecastTool\SupplyChainForecasting>` line back without a red `ERROR`.
   - If it fails with a network/SSL error on a company network, ask IT about the proxy settings for `pip`.

### Step 6: Copy the data and models from the old computer

1. On the **old** computer, open the project folder in File Explorer (Current position: User/alakia/SupplyChainForecasting). Optional : You can just use the dropbox backup. 
2. Copy these folders and files onto a USB drive or shared drive:
   - `data` (required: all plants' raw files and databases)
   - `models` (required: trained models; without them the automation starts from default settings)
   - `reports` (optional: past weekly outputs)
   - `logs` (optional: the history on the Runs page)
   - `.env` (only if you'll run with Docker; it may be hidden, so turn on File Explorer -> View -> Show -> Hidden items)
3. On the **new** computer, paste them into `C:\ForecastTool\SupplyChainForecasting`, so you end up with `C:\ForecastTool\SupplyChainForecasting\data`, `...\models`, etc., right next to the `app` folder. If Windows asks about merging or replacing folders, choose to **replace**.
4. Check: `C:\ForecastTool\SupplyChainForecasting\data\processed` should contain a `.db` file for each plant (e.g. `arlington.db`), and `models\saved_models` should contain `.pkl` files.

Do **not** copy the old computer's `.venv` folder; it won't work on another computer.

### Step 7: Start the app and check it works

1. In Command Prompt (in the project folder, with `(.venv)` showing), run:
   ```
   python app\ui\app.py
   ```
2. After a few seconds it will print `Running on http://127.0.0.1:5000`. If a **Windows Firewall** pop-up appears, click **Allow access**.
3. Open a web browser and go to http://127.0.0.1:5000.
4. Check:
   - [ ] The Home page shows each plant with weeks/parts counts (not zeros).
   - [ ] The Load page's coverage heatmap matches what you saw on the old computer.
   - [ ] The Predict page lists your saved models.
5. To stop the app, click the Command Prompt window and press **Ctrl+C** (or just close the window). Closing the browser tab does **not** stop the app.

If something goes wrong, see `STARTUP_TROUBLESHOOTING.html` in the project folder (double-click it to open it in a browser).

### Step 8: Create a desktop shortcut

So that nobody needs to type commands to start the tool day to day:

1. In Command Prompt, in the project folder, run `notepad Forecast.bat` and click **Yes** to create the file.
2. Paste in exactly:
   ```
   @echo off
   cd /d "%~dp0"
   call .venv\Scripts\activate.bat
   echo Starting the Forecast Tool. Leave this window open while you use the tool.
   echo Close this window to stop the tool.
   start "" cmd /c "timeout /t 8 >nul && start http://127.0.0.1:5000"
   python app\ui\app.py
   pause
   ```
   (`%~dp0` means "the folder this file is in", so it keeps working even if the project folder is moved. The `pause` at the end keeps the window open if the app crashes, so you can read the error.)
3. Save and close Notepad.
4. In File Explorer, go to `C:\ForecastTool\SupplyChainForecasting`, right-click `Forecast.bat` -> **Show more options** (Windows 11) -> **Send to** -> **Desktop (create shortcut)**.
5. Optionally, rename the desktop shortcut to "SCM Forecaster".
6. Double-click the shortcut to test it: a black window opens, and a few seconds later the browser opens on the app. If the browser opens before the app is ready, wait a moment and press **F5**.
   - If Windows shows a blue "Windows protected your PC" screen, click **More info** -> **Run anyway**.


### Step 9 (optional): Schedule the weekly automation

Only do this on the one computer that should process the weekly SCDW files. **Don't schedule it on two computers at once**: the routine *moves* the files out of the folder it reads from, so whichever computer runs first takes them.

1. Open the Start menu, type `Task Scheduler`, and open it.
2. In the right-hand panel, click **Create Task...** (not "Create Basic Task").
3. **General** tab:
   - Name: `Forecast Tool - Arlington weekly`
   - Select **Run whether user is logged on or not** if the computer may be logged out at that time.
4. **Triggers** tab -> **New...**:
   - **Weekly**, tick the day, and set a time after the SCDW files normally land  Click **OK**.
5. **Actions** tab -> **New...**:
   - Action: **Start a program**
   - Program/script: `C:\ForecastTool\SupplyChainForecasting\.venv\Scripts\python.exe`
   - Add arguments: `-m app.routine "<SCDW folder path>" Arlington`
   - Start in: `C:\ForecastTool\SupplyChainForecasting`
   - Click **OK**.
   - Use the full network path for the SCDW folder (like `\\servername\share\SCDW`), not a mapped drive letter like `S:\`. Scheduled tasks often can't see mapped drive letters.
6. Click **OK** and enter your Windows password if asked.
7. Repeat for each plant (one task per plant, with that plant's name at the end of the arguments).
8. Test it: right-click the task -> **Run**. Then open the app's **Runs** page; a new run should appear. If the extract files weren't in the folder, it will show `no_new_data`, which is expected.

### Getting updates later

When the code is changed on GitHub, the new computer doesn't update by itself. To get the latest version:

1. Stop the app (close its window).
2. Open Command Prompt and run:
   ```
   cd /d C:\ForecastTool\SupplyChainForecasting
   .venv\Scripts\activate.bat
   git pull
   pip install -r requirements.txt
   ```
3. Start the app again.

If the update changed the `scikit-learn` or `joblib` version in `requirements.txt`, retrain the models (Retrain page, or let the next automation run do it). Models saved under the old version may not load.

`git pull` never touches `data/`, `models/`, `reports/`, or `logs/`, since Git doesn't track them.

If `git pull` says `Your local changes to the following files would be overwritten`, someone edited code on this computer. **Don't force it.** Stop and contact the maintainer, because those edits will be lost otherwise.

### Rolling back to an older version

If an update breaks something:
1. See the recent versions (press **q** to exit the list):
   ```
   git log --oneline
   ```
   Each line starts with a short code, e.g. `44e41f9 standardize common functions`.
2. Switch to the version you want:
   ```
   git checkout <code>
   ```
3. Restart the app. To go back to the latest version afterwards:
   ```
   git checkout main
   git pull
   ```

Rolling back the code doesn't roll back the data. If a bad version damaged a database, restore `data/processed/<plant>.db` from a backup copy.

### Backups

Since the data and models aren't in Git, the only copy is on the computer that runs the tool. 

# Areas for future improvement

- <b>Alternative cost functions</b>: Currently, the objective function can be chosen between minimizing error (number of units difference between predicted and actual orders).
   - Higher short-supply penalties: Punish the model much more if it underpredicts stock. This will encourage the model to take fewer risks.
   - Part-specific weights: Weight the part numbers such that the most important parts (e.g. by cost or volume) are the most impactful to the model. Error is currently concentrated: a small number of parts (~50) carries most (~60%) of the error.
   - New metrics for error reporting: Mean absolute error and mean squared error consider undershoot/overshoot equivalent. While weighting and penalties will just make the impact of certain parts/undershoot result in a larger MAE for the same amount of error, it might also be useful to look at this in terms of each metric individually.
- <b>Resiliency against incomplete data</b>: The current model updates on a week-by-week basis; we chose this because although data is updated daily, predictions are made per location while ordering is aggregate, making validation per prediction impossible. However, this requires us to wait until all possible week data is known prior to making new predictions or validating old ones.
- <b>Plant-to-plant model transferability</b>: The current model requires part numbers to be distinct to work; duplicated part numbers result in misattributed demand/ordering. While using internal part numbers might enable data to be aggregated, consideration should be given as to the value of insight relating to this. 
- <b>Dynamic cost</b>: When cost data was delivered, I was instructed to only use the latest cost. However, registering costs to the date of the prediction/forecast may be important, I'm not sure.
- <b>Improved augmentation strategies</b>: The current approach uses either last value or prior-year order to project a forward customer order. While this is not the only feature used for these forward projections, these choices can be pretty impactful the further in the future projections are made. For example, for the "last value" strategy, a PN which is otherwise only ordered every other week might end up with a multi-week forecast of zero or double stock.
- <b>Broader hyperparameter search</b>: `app/optimize.py` samples a fixed search space at random. Future work could expand the parameter sets and use a smarter search (e.g. Bayesian or gradient-based).
