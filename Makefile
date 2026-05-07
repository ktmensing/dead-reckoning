VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup fetch build publish all smoke test clean

setup:
	@echo "=== Environment check ==="
	@$(PYTHON) -c "import sys; print('Python', sys.version)"
	@$(PYTHON) -c "import dotenv; dotenv.load_dotenv(); import os; \
		keys = ['FRED_API_KEY','BLS_API_KEY','BEA_API_KEY','EIA_API_KEY']; \
		[print(k + ': SET' if os.getenv(k) else k + ': MISSING') for k in keys]"

fetch:
	@echo "=== Fetching raw data ==="
	$(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
from src.fetch import bls, eia; \
from src.fetch import fred as fred_module; \
import yaml; \
cfg = yaml.safe_load(open('config/series.yaml')); \
comps = cfg['dri_components']; \
bls_ids = [c['series_id'] for c in comps if not c.get('deferred') and c['source'] == 'bls']; \
bls_ids.append(cfg['cpi_headline']['series_id']); \
bls.fetch_batch(bls_ids, start_year=2019); print('BLS done'); \
fred_ids = [c['series_id'] for c in comps if not c.get('deferred') and c['source'] == 'fred' and 'series_id' in c]; \
[fred_ids.extend([i['series_id'] for i in c.get('inputs', []) if i['fetcher'] == 'fred']) for c in comps if c['source'] == 'derived']; \
[fred_module.fetch(sid) for sid in dict.fromkeys(fred_ids)]; print('FRED done'); \
eia_ids = [c['series_id'] for c in comps if not c.get('deferred') and c['source'] == 'eia']; \
[eia.fetch(sid) for sid in eia_ids]; print('EIA done'); \
"

build:
	@echo "=== Building derived data ==="
	$(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
import glob, pandas as pd; \
from src.transform.dri import build_dri; \
from src.store import save_derived; \
import yaml; \
cfg = yaml.safe_load(open('config/series.yaml')); \
# Load from raw cache \
ts = {}; \
comps = cfg['dri_components']; \
for c in comps: \
    if c.get('deferred') or c['source'] == 'derived': continue; \
    sid = c.get('series_id',''); \
    src = c['source']; \
    path = f'data/raw/{src}/{sid}.csv'.replace('.','_') if src == 'eia' else f'data/raw/{src}/{sid}.csv'; \
    try: \
        df = pd.read_csv(path, parse_dates=['date']); ts[c['id']] = df \
    except FileNotFoundError: print(f'Missing cache: {path}'); \
for inp in [i for c in comps if c['source']=='derived' for i in c.get('inputs',[])]: \
    sid=inp['series_id']; path=f'data/raw/fred/{sid}.csv'; \
    try: df=pd.read_csv(path,parse_dates=['date']); ts[sid]=df \
    except: pass; \
cpi_sid=cfg['cpi_headline']['series_id']; \
try: ts['cpi_headline']=pd.read_csv(f'data/raw/bls/{cpi_sid}.csv',parse_dates=['date']) \
except: pass; \
panel, w = build_dri(ts); save_derived('dri_panel', panel); print(f'Built panel: {len(panel)} rows'); \
"

publish:
	@echo "=== Publishing Datawrapper CSVs ==="
	$(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
import pandas as pd; \
from src.store import load_derived; \
from src.publish.datawrapper_csv import publish_dri_vs_cpi, publish_dri_components, publish_dri_component_table; \
from src.transform.dri import build_dri; \
import yaml; \
cfg = yaml.safe_load(open('config/series.yaml')); \
panel = load_derived('dri_panel'); \
comps = cfg['dri_components']; \
present = [c for c in comps if not c.get('deferred') and 'id' in c]; \
raw_w = {c['id']: c['weight'] for c in present if c['id'] in panel.columns}; \
import pandas as pd; w = pd.Series(raw_w); w = w / w.sum(); \
publish_dri_vs_cpi(panel); \
publish_dri_components(panel, w); \
publish_dri_component_table(panel, w); \
print('Published 3 CSVs to data/published/'); \
"

all:
	$(PYTHON) scripts/run_weekly.py

smoke:
	$(PYTHON) scripts/smoke_test_fetchers.py

test:
	$(VENV)/bin/pytest tests/ -v

clean:
	rm -rf data/derived/* data/published/*
	@echo "Cleared data/derived/ and data/published/ (data/raw/ untouched)"
