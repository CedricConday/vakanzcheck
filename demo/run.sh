#!/usr/bin/env bash
# The three demo vacancies in results/. Vacancies and ad texts are invented;
# every number in the reports comes from the live BA services on the run date.
# The ad check uses Claude through `claude -p` (one call per vacancy); drop
# --backend subscription to use the offline keyword check instead.
set -euo pipefail
cd "$(dirname "$0")/.."

vakanzcheck --title "Elektroniker/in für Sicherheitstechnik" \
  --beruf "Elektroniker/in - Energie- und Gebäudetechnik" --wo Paderborn --radius 30 \
  --salary 3300 --per month \
  --ad demo/elektroniker-sicherheitstechnik-paderborn.txt --backend subscription \
  --out results/elektroniker-sicherheitstechnik-paderborn

vakanzcheck --title "Mechatroniker/in Instandhaltung" \
  --beruf "Mechatroniker/in" --wo Bielefeld --radius 25 \
  --salary 23.50 --per hour --hours 35 --extra 2500 \
  --ad demo/mechatroniker-bielefeld.txt --backend subscription \
  --out results/mechatroniker-bielefeld

vakanzcheck --title "CNC-Fräser/in" \
  --beruf "CNC-Fräser/in" --kldb 24232 --wo Gütersloh --radius 25 \
  --salary 4100 --per month --payments 13 \
  --ad demo/cnc-fraeser-guetersloh.txt --backend subscription \
  --out results/cnc-fraeser-guetersloh
