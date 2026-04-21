import sqlite3, json, uuid

conn = sqlite3.connect('data/prism.db')
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT data FROM c2026_plan WHERE id='main'").fetchone()
plan = json.loads(row['data'])

def new_id():
    return 'id_' + uuid.uuid4().hex[:8]

def tbd(team):
    return {'id': new_id(), 'team': team, 'status': 'TBD', 'pct': None, 'days': None, 'start': None, 'end': None}

def get_all_rows(phase):
    """Get all team rows whether flat or staged"""
    if phase.get('stages'):
        rows = []
        for st in phase['stages']:
            rows.extend(st.get('team_rows', []))
        return rows
    return phase.get('team_rows', [])

def update_dep_refs(obj, mapping):
    if isinstance(obj, dict):
        if 'depends_on' in obj and obj['depends_on']:
            for dep in obj['depends_on']:
                if dep.get('ref') in mapping:
                    old = dep['ref']
                    dep['ref'] = mapping[dep['ref']]
                    print(f"  dep ref: {old} -> {dep['ref']}")
        for v in obj.values():
            update_dep_refs(v, mapping)
    elif isinstance(obj, list):
        for item in obj:
            update_dep_refs(item, mapping)

def make_staged(phase, stage1_rows, stage2_rows, phase_name=None):
    """Convert a phase to staged structure"""
    if phase_name:
        phase['name'] = phase_name
    phase['stages'] = [
        {'id': new_id(), 'name': 'Stage 1 - Design', 'team_rows': stage1_rows},
        {'id': new_id(), 'name': 'Stage 2 - Development', 'team_rows': stage2_rows},
    ]
    phase['team_rows'] = []
    return phase

p1 = next(p for p in plan['projects'] if p['id'] == 'c26p1')
p2 = next(p for p in plan['projects'] if p['id'] == 'c26p2')
p3 = next(p for p in plan['projects'] if p['id'] == 'c26p3')
p4 = next(p for p in plan['projects'] if p['id'] == 'c26p4')

# ── P1 Audit: Phase 3 ─────────────────────────────────────────────────
ph3 = next(ph for ph in p1['phases'] if ph['id'] == 'c26ph3')
# Current rows: BA(done), UI/UX(done), FE(est), BE(est), QC(est) - all in flat team_rows
ph3_rows = list(ph3.get('team_rows', []))
ph3_dev = [tbd('FE'), tbd('BE'), tbd('QC')]
make_staged(ph3, ph3_rows, ph3_dev, 'Phase 3 - Reporting & Dashboards')
print(f"P1 Ph3 -> staged: Stage1={[r['team'] for r in ph3_rows]}, Stage2=[FE,BE,QC empty]")

# ── P1 Audit: Phase 4 ─────────────────────────────────────────────────
ph4 = next(ph for ph in p1['phases'] if ph['id'] == 'id_zm50wtng')
# Current rows after earlier migration: FE, BE, QC only (BA/UI/UX were removed)
# We need to restore BA and UI/UX with their original data
ba_row = {
    'id': 'id_qej6bv03', 'team': 'BA', 'status': 'TBD', 'pct': None, 'days': 5,
    'start': '2026-04-19', 'end': '2026-04-23',
    'depends_on': [{'ref': 'P2.Ph1.St1.BA', 'type': 'FS', 'lag': 0}]
}
uiux_row = {
    'id': 'id_8as010v5', 'team': 'UI/UX', 'status': 'TBD', 'pct': None, 'days': 5,
    'start': '2026-05-03', 'end': '2026-05-07',
    'depends_on': [{'ref': 'P2.Ph1.St1.UIUX', 'type': 'FS', 'lag': 0}]
}
ph4_design = [ba_row, uiux_row, tbd('FE'), tbd('BE'), tbd('QC')]
ph4_dev = list(ph4.get('team_rows', []))  # current FE, BE, QC dev rows
make_staged(ph4, ph4_design, ph4_dev, 'Phase 4 - Audit Management Module (V2)')
print(f"P1 Ph4 -> staged: Stage1=[BA,UI/UX,FE,BE,QC], Stage2={[r['team'] for r in ph4_dev]}")

# ── P2 Shared Functions: merge Phase 1 + Phase 2 into single staged phase ──
p2_ph1 = next(ph for ph in p2['phases'] if ph['id'] == 'c26ph4')
p2_ph2_id = next((ph['id'] for ph in p2['phases'] if ph['id'] != 'c26ph4'), None)
if p2_ph2_id:
    p2_ph2 = next(ph for ph in p2['phases'] if ph['id'] == p2_ph2_id)
    p2_design_rows = list(p2_ph1.get('team_rows', []))
    p2_dev_rows = list(p2_ph2.get('team_rows', []))
    make_staged(p2_ph1, p2_design_rows, p2_dev_rows, 'Phase 1 - Actions Management Module')
    p2['phases'] = [ph for ph in p2['phases'] if ph['id'] != p2_ph2_id]
    print(f"P2 -> merged phases. Stage1={[r['team'] for r in p2_design_rows]}, Stage2={[r['team'] for r in p2_dev_rows]}")
else:
    # Only 1 phase, convert flat to staged
    p2_rows = list(p2_ph1.get('team_rows', []))
    design = [r for r in p2_rows if r['team'] in ('BA','UI/UX')]
    dev = [r for r in p2_rows if r['team'] not in ('BA','UI/UX')]
    if not dev:
        dev = [tbd('FE'), tbd('BE'), tbd('QC')]
    make_staged(p2_ph1, design, dev, 'Phase 1 - Actions Management Module')
    print(f"P2 -> converted single phase to staged")

# ── P3 CUBES Intelligence: merge Phase 1 + Phase 2 ────────────────────
p3_ph1 = next(ph for ph in p3['phases'] if ph['id'] == 'c26ph5')
p3_ph2_id = next((ph['id'] for ph in p3['phases'] if ph['id'] != 'c26ph5'), None)
if p3_ph2_id:
    p3_ph2 = next(ph for ph in p3['phases'] if ph['id'] == p3_ph2_id)
    p3_design = list(p3_ph1.get('team_rows', []))
    p3_dev = list(p3_ph2.get('team_rows', []))
    make_staged(p3_ph1, p3_design, p3_dev, 'Phase 1 - AI & Analytics')
    p3['phases'] = [ph for ph in p3['phases'] if ph['id'] != p3_ph2_id]
    print(f"P3 -> merged phases. Stage1={[r['team'] for r in p3_design]}, Stage2={[r['team'] for r in p3_dev]}")
else:
    p3_rows = list(p3_ph1.get('team_rows', []))
    design = [r for r in p3_rows if r['team'] in ('BA','UI/UX')]
    dev = [r for r in p3_rows if r['team'] not in ('BA','UI/UX')]
    if not dev:
        dev = [tbd('FE'), tbd('BE'), tbd('QC')]
    make_staged(p3_ph1, p3_rows, dev, 'Phase 1 - AI & Analytics')
    print(f"P3 -> single phase staged")

# ── P4 Performance Revamp: merge Phase 1 + Phase 2 ────────────────────
p4_ph1 = next(ph for ph in p4['phases'] if ph['id'] == 'c26ph6')
p4_ph2_id = next((ph['id'] for ph in p4['phases'] if ph['id'] != 'c26ph6'), None)
if p4_ph2_id:
    p4_ph2 = next(ph for ph in p4['phases'] if ph['id'] == p4_ph2_id)
    p4_design = list(p4_ph1.get('team_rows', []))
    p4_dev = list(p4_ph2.get('team_rows', []))
    make_staged(p4_ph1, p4_design, p4_dev)
    p4['phases'] = [ph for ph in p4['phases'] if ph['id'] != p4_ph2_id]
    print(f"P4 -> merged phases. Stage1={[r['team'] for r in p4_design]}, Stage2={[r['team'] for r in p4_dev]}")
else:
    p4_rows = list(p4_ph1.get('team_rows', []))
    design = p4_rows
    make_staged(p4_ph1, design, [tbd('FE'), tbd('BE'), tbd('QC')])
    print(f"P4 -> single phase staged")

# ── Update all remaining dep refs (P1.Ph4.* and P4.Ph1.*) ─────────────
ref_mapping = {
    'P1.Ph4.BA':   'P1.Ph4.St1.BA',
    'P1.Ph4.UIUX': 'P1.Ph4.St1.UIUX',
    'P1.Ph4.FE':   'P1.Ph4.St1.FE',
    'P4.Ph1.BA':   'P4.Ph1.St1.BA',
    'P4.Ph1.UIUX': 'P4.Ph1.St1.UIUX',
    'P4.Ph1.FE':   'P4.Ph1.St1.FE',
}
print("\nUpdating dependency refs:")
update_dep_refs(plan, ref_mapping)

# ── Save ──────────────────────────────────────────────────────────────
conn.execute("UPDATE c2026_plan SET data=? WHERE id='main'", (json.dumps(plan),))
conn.commit()
conn.close()

print("\nDone! Verifying structure:")
for proj in plan['projects']:
    print(f"\n{proj['name']}:")
    for ph in proj['phases']:
        if ph.get('stages'):
            print(f"  [{ph['name']}] (staged)")
            for st in ph['stages']:
                print(f"    {st['name']}: {[r['team'] for r in st['team_rows']]}")
        else:
            print(f"  [{ph['name']}] (flat): {[r['team'] for r in ph.get('team_rows',[])]}")
