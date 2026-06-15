from pathlib import Path
# Paths

APP_DIR = Path(__file__).resolve().parent   # app/
ROOT_DIR = APP_DIR.parent                   # project root

dirs = {
    'app': APP_DIR,
    'data': ROOT_DIR / 'data',          # root-level data dir; plant files at data/{plant}/raw/{type}/
    'ext_data': ROOT_DIR / 'data',      # alias used by upload routes
    'reports': ROOT_DIR / 'reports',
    'figures': APP_DIR / 'figures',
}

dirs.update({
    'processed': dirs['data'] / 'processed',                  # .db files per plant land here
    'saved_models': ROOT_DIR / 'models' / 'saved_models',     # matches docker volume ./models
})


PLANT_SOURCES = ['Arlington', 'Hermasillo']

# pre dictionary
pre = {
    'pp': 'pred',
    'op': 'order',
    'rd': 'reldate',
    'ys': 'year',
    'ws': 'week',
    'qs': 'qty',
    'ds': 'idx'
}
pre.update({
    'pi': pre['pp'] + pre['ds'],
    'oi': pre['op'] + pre['ds'],
    'py': pre['pp'] + pre['ys'],
    'oy': pre['op'] + pre['ys'],
    'pw': pre['pp'] + pre['ws'],
    'ow': pre['op'] + pre['ws'],
    'pq': pre['pp'] + pre['qs'],
    'oq': pre['op'] + pre['qs'],
})

