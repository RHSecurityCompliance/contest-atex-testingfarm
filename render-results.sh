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

if [[ $FAIL_FOUND == false ]]; then
  where="subtest IS NULL"  # show passed tests
else
  where="status NOT IN ('pass', 'warn', 'skip')"  # show only failing
fi

# pre-fill the SQL filter since the index.html will be embedded within
# the Oculus viewer without us being able to give it ?q= upfront
# - only do that if the user hasn't pre-set query via direct URL
sed "/DOMContentLoaded/a if (get_url_query() === null) set_url_query(\"$where ORDER BY platform, test\");" \
  -i output/index.html

mv -v output/* "$TMT_TEST_DATA/."
