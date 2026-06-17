from pathlib import Path
# Paths

APP_DIR = Path(__file__).resolve().parent
PROJ_DIR = APP_DIR.resolve().parent

dirs = {
    'app': APP_DIR,
    'data': PROJ_DIR / 'data',
    'reports': PROJ_DIR / "reports",
}

dirs.update({
    'processed': dirs['data'] / 'processed',
    'saved_models': APP_DIR / 'models' / 'saved_models',
})

# INCLUDE NEW PLACES HERE !
PLANT_SOURCES = ['Arlington', 'Hermasillo']


def raw_dir(plant: str, datatype: str = None) -> Path:
    """Returns data/{plant} or data/{plant}/{datatype}."""
    p = dirs['data'] / plant
    return p / datatype if datatype else p


def plant_db(plant: str) -> Path:
    """Returns data/processed/{plant}.db."""
    return dirs['processed'] / f'{plant.strip().lower()}.db'


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
