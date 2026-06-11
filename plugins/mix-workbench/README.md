# Mix Workbench (dashboard plugin)

Read-only browser over `/Users/rolly/Build/mix/mix-mono/workbench` (SUE-75):
UI screen captures (refreshed every iOS pre-push), walk-eval experiment
runs, simulator transcripts. Tab: /mix-workbench; API:
/api/plugins/mix-workbench/{runs,file} (cookie-auth like every dashboard
route; file serving is suffix-allowlisted + traversal-safe).

Groups are deliberately curated in plugin_api.py (GROUPS) — extend there
when new artifact families appear (Android emulator screenshots next).
After editing: restart the dashboard (no launchd on this host — see the
hermes-dashboard-restart memory for the nohup relaunch line).
