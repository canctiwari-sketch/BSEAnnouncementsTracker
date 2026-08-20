"""Scheduled entry point: check for newly filed annual reports and mine them.

Designed to run every day. The work splits into two cadences:

  weekly   Re-screen the universe -- market caps drift across the Rs 100 cr
           floor, and a company that started holding concalls should drop off
           the silent list. Also refreshes the BSE/NSE cross-reference.
  daily    Look for annual reports that appeared since the last run, download
           and mine the new ones, rebuild the HTML.

The daily half is cheap because discover.py only re-checks companies whose
report has not been found yet, and mine.py skips anything already mined. A day
with no new filings costs a few minutes of API calls and changes nothing.

Run with --weekly to force the weekly stages regardless of the day.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "data", "annual_reports")
REPORTS = os.path.join(DATA, "fy2026_reports.jsonl")
SCREEN = os.path.join(DATA, "screen.jsonl")

WEEKLY_AGE_DAYS = 7


def run(script, env_extra=None):
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update(env_extra)
    print("\n=== {} ===".format(script), flush=True)
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, script)], env=env, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit("{} failed with exit code {}".format(script, result.returncode))


def found_keys():
    if not os.path.exists(REPORTS):
        return set()
    keys = set()
    with open(REPORTS, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "found":
                keys.add(rec["key"])
    return keys


def screen_age_days():
    if not os.path.exists(SCREEN):
        return 10 ** 6
    mtime = datetime.fromtimestamp(os.path.getmtime(SCREEN))
    return (datetime.now() - mtime).days


def main():
    force_weekly = "--weekly" in sys.argv
    age = screen_age_days()
    do_weekly = force_weekly or age >= WEEKLY_AGE_DAYS

    print("annual report watch | {} | screen is {} day(s) old | weekly stages: {}"
          .format(date.today().isoformat(),
                  age if age < 10 ** 6 else "never built",
                  "yes" if do_weekly else "no"), flush=True)

    if do_weekly:
        run("xref.py")
        run("nse_engagement.py")
        run("screen.py")

    before = found_keys()
    run("discover.py", {"AR_SILENT_ONLY": "1"})
    after = found_keys()
    new = after - before

    if new:
        print("\n{} new annual report(s) since last run".format(len(new)), flush=True)
        run("mine.py")
        run("build_report.py")
    else:
        print("\nno new annual reports today -- nothing to mine", flush=True)
        # Still rebuild if mining was left half-done by an interrupted run.
        run("mine.py")
        run("build_report.py")

    print("\nwatch complete: {} reports tracked in total".format(len(after)))


if __name__ == "__main__":
    main()
