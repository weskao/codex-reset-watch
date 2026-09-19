"""Windows Task Scheduler command-building for the `schtasks` CLI.

Kept separate from install.py so the argv-building logic is importable and
unit-testable on every OS, even though the commands only run on Windows.
Secrets are not passed as task arguments (they would leak into `schtasks
/query /v` output); install.py persists TG_BOT_TOKEN/TG_CHAT_ID with `setx`
into the user's environment instead, same as any other Windows scheduled task.
"""
DAILY_TASK_NAME = "CodexResetWatchDaily"
MONITOR_TASK_NAME = "CodexResetWatchMonitor"


def daily_task_command(program, task_name=DAILY_TASK_NAME):
    return [
        "schtasks", "/Create", "/F", "/TN", task_name,
        "/TR", f'"{program}" daily',
        "/SC", "DAILY", "/ST", "10:00",
    ]


def monitor_task_command(program, task_name=MONITOR_TASK_NAME):
    return [
        "schtasks", "/Create", "/F", "/TN", task_name,
        "/TR", f'"{program}" monitor',
        "/SC", "HOURLY", "/MO", "2", "/ST", "00:05",
    ]


def delete_task_command(task_name):
    return ["schtasks", "/Delete", "/F", "/TN", task_name]
