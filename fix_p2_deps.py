import sqlite3, json
conn = sqlite3.connect('data/prism.db')
row = conn.execute("SELECT data FROM c2026_plan WHERE id='main'").fetchone()
plan = json.loads(row[0])
p2 = next(p for p in plan['projects'] if p['id'] == 'c26p2')
ph1 = p2['phases'][0]
mapping = {'P2.Ph1.UIUX': 'P2.Ph1.St1.UIUX', 'P2.Ph1.BA': 'P2.Ph1.St1.BA'}
for st in ph1.get('stages', []):
    for r in st['team_rows']:
        for dep in r.get('depends_on') or []:
            if dep['ref'] in mapping:
                print(f"Fix {r['team']}: {dep['ref']} -> {mapping[dep['ref']]}")
                dep['ref'] = mapping[dep['ref']]
conn.execute("UPDATE c2026_plan SET data=? WHERE id='main'", (json.dumps(plan),))
conn.commit()
conn.close()
print('Done')
