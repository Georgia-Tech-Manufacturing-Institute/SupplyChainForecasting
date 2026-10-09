import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJ_DIR = APP_DIR.resolve().parent

# INCLUDE NEW PLACES HERE !
plant_key = {'alma': '485', 
             'anniston': 'AM', 
             'arlington': 'AT', 
             'cottondale': 'AN', 
             'hermosillo': 'CU3', 
             'mendon': 'H8', 
             'monterrey': '4753', 
             'north monterrey': 'NM3', 
             'port huron': '483', 
             'saltillo': 'CV3', 
             'toluca': 'OG3', 
             'warren': '493', 
             'windsor': 'KD'}

_BASE_PLANT_SOURCES = ['Arlington', 
                       'Hermasillo',
                       'Cottondale',
                       ]


dirs = {
    'app': APP_DIR,
    'data': PROJ_DIR / 'data',
    'reports': PROJ_DIR / "reports",
}

dirs.update({
    'processed': dirs['data'] / 'processed',
    'incoming': dirs['data'] / 'incoming',   # default folder routine.py checks for new extracts
    'saved_models': PROJ_DIR / 'models' / 'saved_models',
    'run_logs': PROJ_DIR / 'logs' / 'routine_runs',
})



def add_plant(plant: str) -> None:
    """Register a new plant source, persisting it alongside the hardcoded
    defaults so it shows up in PLANT_SOURCES on future runs too."""
    global PLANT_SOURCES
    plant = plant.strip()
    if not plant:
        raise ValueError("Plant name cannot be empty.")
    if plant.lower() in [p.lower() for p in PLANT_SOURCES]:
        raise ValueError(f"Plant '{plant}' already exists.")

    dirs['data'].mkdir(parents=True, exist_ok=True)

    PLANT_SOURCES = _BASE_PLANT_SOURCES 


PLANT_SOURCES = _BASE_PLANT_SOURCES 



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
