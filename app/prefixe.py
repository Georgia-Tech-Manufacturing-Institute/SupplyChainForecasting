from pathlib import Path
# Paths

APP_DIR = Path(__file__).resolve().parent

dirs = {
    'app': APP_DIR,
    'ext_data': APP_DIR.resolve().parent / 'data',
    'data': APP_DIR / "data",
    'reports': APP_DIR / "reports",
    'figures': APP_DIR / 'figures'
    }

dirs.update({
    'raw': dirs['data'] / 'raw',
    'processed': dirs['data'] / 'processed',
    'ext_processed': dirs['ext_data'] / 'processed'

             })
dirs.update({
    'cost': dirs['raw'] / 'Cost.txt',
    'saved_models': APP_DIR / 'models' / 'saved_models',
    })


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

