#!/usr/bin/env python3
"""
Returns exit code 0 if today (US/Eastern date) is a US market trading day,
exit code 1 if it's a weekend or NYSE holiday.

Self-contained — no external deps. Holidays generated from NYSE rules.

Usage:
    python3 ~/Desktop/ib_algo_trader/is_market_day.py && python3 some_other_script.py
"""
import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def nth_weekday(year, month, weekday, n):
    """nth occurrence of weekday in month. weekday: Mon=0...Sun=6."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def last_weekday(year, month, weekday):
    """Last occurrence of weekday in month."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def observed(d):
    """If holiday falls on Sat → observed Fri. If Sun → observed Mon."""
    if d.weekday() == 5:  # Sat
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sun
        return d + timedelta(days=1)
    return d


def easter(year):
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    L = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * L) // 451
    month = (h + L - 7 * m + 114) // 31
    day = ((h + L - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nyse_holidays(year):
    """Full NYSE closed dates for the year."""
    h = set()
    h.add(observed(date(year, 1, 1)))                      # New Year's
    h.add(nth_weekday(year, 1, 0, 3))                      # MLK Day (3rd Mon Jan)
    h.add(nth_weekday(year, 2, 0, 3))                      # Presidents Day (3rd Mon Feb)
    h.add(easter(year) - timedelta(days=2))                # Good Friday
    h.add(last_weekday(year, 5, 0))                        # Memorial Day (last Mon May)
    h.add(observed(date(year, 6, 19)))                     # Juneteenth
    h.add(observed(date(year, 7, 4)))                      # Independence Day
    h.add(nth_weekday(year, 9, 0, 1))                      # Labor Day (1st Mon Sep)
    h.add(nth_weekday(year, 11, 3, 4))                     # Thanksgiving (4th Thu Nov)
    h.add(observed(date(year, 12, 25)))                    # Christmas
    return h


def is_trading_day(d):
    if d.weekday() >= 5:  # Sat=5, Sun=6
        return False, "weekend"
    if d in nyse_holidays(d.year):
        return False, "NYSE holiday"
    return True, "trading day"


if __name__ == "__main__":
    today_et = datetime.now(ET).date()
    open_, reason = is_trading_day(today_et)
    if open_:
        print(f"OPEN: NYSE trades today ({today_et} ET) — {reason}")
        sys.exit(0)
    else:
        print(f"CLOSED: NYSE not trading ({today_et} ET) — {reason}")
        sys.exit(1)
