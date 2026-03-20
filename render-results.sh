#!/bin/bash

atex_html=$(mktemp -d)
trap "rm -rf '$atex_html'" EXIT

git clone --depth 1 https://github.com/RHSecurityCompliance/atex-html.git "$atex_html"

mkdir output

"$atex_html/json2db.py" runs/results.json.xz output/results.sqlite.gz
mv -v runs/files output/files_dir
cp -rv "$atex_html"/{index.html,sqljs} output/.

if [[ -d old_runs ]]; then
  mkdir -p output/old_runs
  "$atex_html/json2db.py" \
    old_runs/results.json.xz \
    output/old_runs/results.sqlite.gz
  mv -v old_runs/files output/old_runs/files_dir
  cp -rv "$atex_html"/{index.html,sqljs} output/old_runs/.
fi

# pre-fill the SQL filter since the index.html will be embedded within
# the Oculus viewer without us being able to give it ?q= upfront
sed "/DOMContentLoaded/a set_url_query(\"status NOT IN ('pass', 'warn', 'skip')\");" \
  -i output/index.html

mv -v output/* "$TMT_TEST_DATA/."
