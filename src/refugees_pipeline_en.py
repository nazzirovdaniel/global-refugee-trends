"""
UNHCR data preparation for the Power BI dashboard
"Global Refugee Trends 2015-2025".

What the script does:
  1. Reads two UNHCR Refugee Data Finder exports:
       asylum_applications.csv  - asylum applications
       persons_of_concern.csv   - refugees, asylum-seekers, IDPs, others in need of protection
  2. Keeps only NEW applications counted in persons (type 'N', unit 'P').
  3. Separates cross-border data from internally displaced persons (IDPs):
     origin country == asylum country means IDPs, not refugees.
  4. Assigns regions and a "Europe group" (EU-27, EFTA + UK, ...).
  5. Builds an applications-by-country table that converts US "cases" to persons
     (factors taken from UNHCR footnotes).
  6. Uses readable English country names.
  7. Verifies that cleaned totals equal the raw-file totals.

Run (input files next to the script):
    pip install pandas pycountry-convert babel
    python refugees_pipeline_en.py
or with paths:
    python refugees_pipeline_en.py --asylum path/asylum_applications.csv --pop path/persons_of_concern.csv --out output
"""
import argparse
import os

import pandas as pd
import pycountry_convert as pc
from babel import Locale

EU27 = set("AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE".split())
EFTA_UK = set("ISL LIE NOR CHE GBR".split())
CONTINENTS = {'EU': 'Europe', 'AS': 'Asia', 'AF': 'Africa', 'NA': 'North America', 'SA': 'South America', 'OC': 'Oceania'}

# Manual region fixes: the library puts Cyprus in Asia and does not know Sint Maarten;
# Türkiye is shown as its own category (largest host).
REGION_FIX = {'CYP': 'Europe', 'SXM': 'North America', 'TUR': 'Türkiye'}
REGION_EN = {'Other/Unknown': 'Unspecified'}

EN_TERRITORIES = Locale('en').territories
MANUAL_ISO2 = {'XKX': 'XK'}
SPECIAL_NAMES = {'Unknown': 'Unspecified', 'Stateless': 'Stateless persons', 'Tibetan': 'Tibetans',
                 'Various/unknown': 'Unspecified'}
NAME_OVERRIDE = {'TUR': 'Türkiye', 'VAT': 'Vatican City', 'XKX': 'Kosovo', 'PSE': 'Palestine', 'COD': 'DR Congo',
                 'COG': 'Republic of the Congo', 'CIV': "Côte d'Ivoire", 'SYR': 'Syria', 'IRN': 'Iran',
                 'VEN': 'Venezuela', 'MMR': 'Myanmar', 'RUS': 'Russia', 'USA': 'United States',
                 'GBR': 'United Kingdom', 'KOR': 'South Korea', 'PRK': 'North Korea', 'MKD': 'North Macedonia',
                 'MDA': 'Moldova', 'TZA': 'Tanzania', 'BOL': 'Bolivia', 'LAO': 'Laos', 'CZE': 'Czechia',
                 'SWZ': 'Eswatini', 'CPV': 'Cabo Verde', 'FSM': 'Micronesia', 'BRN': 'Brunei',
                 'TLS': 'Timor-Leste', 'HKG': 'Hong Kong', 'MAC': 'Macao', 'CAF': 'Central African Republic',
                 'SSD': 'South Sudan', 'DOM': 'Dominican Republic', 'ARE': 'United Arab Emirates'}



def continent(iso3):
    """ISO-3 country code -> continent name, or 'Other/Unknown'."""
    try:
        iso2 = pc.country_alpha3_to_country_alpha2(iso3)
        return CONTINENTS[pc.country_alpha2_to_continent_code(iso2)]
    except Exception:
        return 'Other/Unknown'


def apply_region_fix(df, iso_col, region_col):
    for iso, region in REGION_FIX.items():
        df.loc[df[iso_col] == iso, region_col] = region
    return df


def europe_group(iso, region):
    """Slicer group: EU-27, EFTA + UK, other Europe, Türkiye, outside Europe."""
    if iso in EU27:
        return 'EU-27'
    if iso in EFTA_UK:
        return 'EFTA + UK'
    if region == 'Europe':
        return 'Other Europe (Balkans, Ukraine, Moldova, Belarus, Russia)'
    if region == 'Türkiye':
        return 'Türkiye'
    return 'Outside Europe'


def case_factor(iso, year):
    """Persons per US asylum 'case' (UNHCR footnotes): 2016: 1.46; 2018-2019: 1.501; 2020: 1.5;
    2025: 1.0 (one case = one person). 2015 and 2017 have no footnote, 1.46 assumed. Other countries: 1.0."""
    if iso != 'USA':
        return 1.0
    if year == 2025:
        return 1.0
    if year in (2018, 2019):
        return 1.501
    if year == 2020:
        return 1.5
    return 1.46


def english_name(iso, raw):
    e = (raw or '').strip()
    if e in SPECIAL_NAMES:
        return SPECIAL_NAMES[e]
    if isinstance(iso, str) and iso.strip():
        if iso in NAME_OVERRIDE:
            return NAME_OVERRIDE[iso]
        try:
            iso2 = MANUAL_ISO2.get(iso) or pc.country_alpha3_to_country_alpha2(iso)
            if iso2 in EN_TERRITORIES:
                return EN_TERRITORIES[iso2]
        except Exception:
            pass
    return e or None


def add_names(df, iso_col, name_col, new_col):
    cache = {}
    vals = []
    for iso, name in zip(df[iso_col], df[name_col]):
        key = (iso, name)
        if key not in cache:
            cache[key] = english_name(iso, name)
        vals.append(cache[key])
    df[new_col] = vals
    missing = sorted({(str(k[0]), str(k[1])) for k, v in cache.items() if not v})
    if missing:
        raise ValueError(f'No English name for: {missing}')


ASYLUM_COLUMNS = ['Year', 'Country of Asylum', 'Country of Origin', 'Country of Asylum ISO', 'Country of Origin ISO',
                  'Application type', 'App_pc', 'Applied']
POP_COLUMNS = ['Year', 'Country of Asylum', 'Country of Origin', 'Country of Asylum ISO', 'Country of Origin ISO',
               'Refugees', 'Asylum-seekers', 'IDPs', 'Other people in need of international protection']


def require_columns(df, needed, path):
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise SystemExit(
            f'Error: {path} has no columns {missing}.\n'
            'Inputs must be the ORIGINAL UNHCR files asylum_applications.csv and persons_of_concern.csv '
            '(first line starts with Year, "Country of Asylum", ...), not this script\'s output. '
            'Also check that --asylum and --pop are not swapped.')


def main(asylum_path, pop_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    a = pd.read_csv(asylum_path, encoding='utf-8-sig')
    p = pd.read_csv(pop_path, encoding='utf-8-sig')
    require_columns(a, ASYLUM_COLUMNS, asylum_path)
    require_columns(p, POP_COLUMNS, pop_path)

    # Step 1. New applications in persons ('N' = new, no appeals/repeats; 'P' = persons, not family cases)
    new_p = a[(a['Application type'] == 'N') & (a['App_pc'] == 'P')]
    na = (new_p.groupby(['Year', 'Country of Asylum', 'Country of Asylum ISO',
                         'Country of Origin', 'Country of Origin ISO'], as_index=False).Applied.sum()
          .rename(columns={'Applied': 'New_applications'}))
    na['Asylum_region'] = na['Country of Asylum ISO'].map(continent)

    # Step 2. Refugees (cross-border) vs IDPs (inside own country)
    p['Same'] = p['Country of Asylum'] == p['Country of Origin']
    idp = (p[p['Same'] & (p['IDPs'] > 0)][['Year', 'Country of Asylum', 'Country of Asylum ISO', 'IDPs']]
           .rename(columns={'Country of Asylum': 'Country', 'Country of Asylum ISO': 'ISO'}))
    st = p[~p['Same']][['Year', 'Country of Asylum', 'Country of Asylum ISO', 'Country of Origin',
                        'Country of Origin ISO', 'Refugees', 'Asylum-seekers',
                        'Other people in need of international protection']].copy()
    st.columns = ['Year', 'Country of Asylum', 'Country of Asylum ISO', 'Country of Origin', 'Country of Origin ISO',
                  'Refugees', 'Asylum_seekers', 'Other_in_need_of_protection']
    st['Asylum_region'] = st['Country of Asylum ISO'].map(continent)
    st['Origin_region'] = st['Country of Origin ISO'].map(continent)
    st = st[(st.Refugees > 0) | (st.Asylum_seekers > 0) | (st.Other_in_need_of_protection > 0)]

    # Step 3. Region fixes and Europe group
    na = apply_region_fix(na, 'Country of Asylum ISO', 'Asylum_region')
    st = apply_region_fix(st, 'Country of Asylum ISO', 'Asylum_region')
    st = apply_region_fix(st, 'Country of Origin ISO', 'Origin_region')
    for df in (na, st):
        df['Europe_group'] = [europe_group(i, r) for i, r in zip(df['Country of Asylum ISO'], df['Asylum_region'])]

    # Step 4. Applications by asylum country with US cases converted to persons.
    # Cannot be split by origin: the persons-per-case factor is valid only for the asylum country as a whole.
    new_all = a[a['Application type'] == 'N']
    g = new_all.pivot_table(index=['Year', 'Country of Asylum', 'Country of Asylum ISO'],
                            columns='App_pc', values='Applied', aggfunc='sum', fill_value=0).reset_index()
    g.columns.name = None
    g = g.rename(columns={'P': 'Persons', 'C': 'Cases'})
    if 'Cases' not in g:
        g['Cases'] = 0
    g['Factor'] = [case_factor(i, y) for i, y in zip(g['Country of Asylum ISO'], g['Year'])]
    g['Applications_adj'] = (g.Persons + g.Cases * g.Factor).round().astype(int)
    reg = na[['Country of Asylum ISO', 'Asylum_region', 'Europe_group']].drop_duplicates('Country of Asylum ISO')
    g = g.merge(reg, on='Country of Asylum ISO', how='left')
    no_region = g.Asylum_region.isna()
    g.loc[no_region, 'Asylum_region'] = [REGION_FIX.get(i, continent(i)) for i in g.loc[no_region, 'Country of Asylum ISO']]
    g['Europe_group'] = [europe_group(i, r) for i, r in zip(g['Country of Asylum ISO'], g['Asylum_region'])]
    ad = g[['Year', 'Country of Asylum', 'Country of Asylum ISO', 'Asylum_region', 'Europe_group',
            'Persons', 'Cases', 'Factor', 'Applications_adj']].copy()

    # Step 5. Checks: cleaned totals == raw totals
    assert new_p.Applied.sum() == na.New_applications.sum(), 'New applications total mismatch'
    cross = p[~p['Same']]
    for raw_col, new_col in (('Refugees', 'Refugees'), ('Asylum-seekers', 'Asylum_seekers'),
                             ('Other people in need of international protection', 'Other_in_need_of_protection')):
        assert cross[raw_col].sum() == st[new_col].sum(), f'Total mismatch: {raw_col}'
    assert p['IDPs'].sum() == idp['IDPs'].sum(), 'IDP total mismatch'
    assert na.duplicated(['Year', 'Country of Asylum ISO', 'Country of Origin ISO']).sum() == 0, 'Duplicates found'
    print('Checks passed: cleaned totals equal raw-file totals.')

    # Step 6. English names and final column names
    add_names(st, 'Country of Asylum ISO', 'Country of Asylum', 'Asylum_country')
    add_names(st, 'Country of Origin ISO', 'Country of Origin', 'Origin_country')
    add_names(na, 'Country of Asylum ISO', 'Country of Asylum', 'Asylum_country')
    add_names(na, 'Country of Origin ISO', 'Country of Origin', 'Origin_country')
    add_names(ad, 'Country of Asylum ISO', 'Country of Asylum', 'Asylum_country')
    add_names(idp, 'ISO', 'Country', 'Country')
    for df in (st, na, ad):
        for col in ('Asylum_region', 'Origin_region'):
            if col in df:
                df[col] = df[col].replace(REGION_EN)

    out = {
        'refugees_stock': st.rename(columns={'Country of Asylum ISO': 'Asylum_ISO', 'Country of Origin ISO': 'Origin_ISO'})[
            ['Year', 'Asylum_country', 'Asylum_ISO', 'Origin_country', 'Origin_ISO', 'Refugees', 'Asylum_seekers',
             'Other_in_need_of_protection', 'Asylum_region', 'Origin_region', 'Europe_group']],
        'asylum_new_applications': na.rename(columns={'Country of Asylum ISO': 'Asylum_ISO',
                                                      'Country of Origin ISO': 'Origin_ISO'})[
            ['Year', 'Asylum_country', 'Asylum_ISO', 'Origin_country', 'Origin_ISO', 'New_applications',
             'Asylum_region', 'Europe_group']],
        'asylum_by_country_year': ad.rename(columns={'Country of Asylum ISO': 'Asylum_ISO', 'Persons': 'Applications_persons',
                                                     'Cases': 'Applications_cases', 'Factor': 'Case_factor',
                                                     'Applications_adj': 'Applications_adjusted'})[
            ['Year', 'Asylum_country', 'Asylum_ISO', 'Asylum_region', 'Europe_group', 'Applications_persons',
             'Applications_cases', 'Case_factor', 'Applications_adjusted']],
        'idps_by_country': idp[['Year', 'Country', 'ISO', 'IDPs']],
    }

    # Step 7. Save (UTF-8 with BOM so Excel/Power BI read special characters)
    for name, df in out.items():
        df.to_csv(os.path.join(out_dir, f'{name}.csv'), index=False, encoding='utf-8-sig')
        print(f'{name}.csv: {len(df)} rows')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Prepare UNHCR data for Power BI (English)')
    ap.add_argument('--asylum', default='asylum_applications.csv')
    ap.add_argument('--pop', default='persons_of_concern.csv')
    ap.add_argument('--out', default='output')
    args = ap.parse_args()
    main(args.asylum, args.pop, args.out)