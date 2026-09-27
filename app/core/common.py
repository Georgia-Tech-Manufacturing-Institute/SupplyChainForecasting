

def idx_to_week(v):
    return (v // 52, v % 52)

def preformat_plant(s):
    return s.strip().lower()

def week_to_idx(year, week):
    return int(year) * 52 + int(week)
