#!/usr/bin/env bash
# Проверки версии на CI (одинаково на Windows и macOS): все наборы тестов,
# точность поиска по фото на настоящих снимках (встроенный алгоритм и
# нейросеть), нагрузка на 1 000 / 5 000 / 10 000 предметов, снимки экранов.
# Результаты складываются в папку out/ — её CI публикует в ветку ci-renders.
set +e
export QT_QPA_PLATFORM=offscreen PYTHONUTF8=1
mkdir -p out out/renders
: > out/tests_summary.txt
failed=0
for f in tests/test_*.py tests/check_imports.py; do
  mod=$(echo "${f%.py}" | tr '/' '.')
  b=$(basename "${f%.py}")
  if [ "$b" = "test_v40_ui" ]; then
    python -m "$mod" --report out/button_audit.md > "out/$b.log" 2>&1
  else
    python -m "$mod" > "out/$b.log" 2>&1
  fi
  code=$?
  ok=$(grep -c "\[OK\]\|^  ok " "out/$b.log" || true)
  fail=$(grep -c "\[FAIL\]\|^  FAIL" "out/$b.log" || true)
  echo "$mod exit=$code ok=$ok fail=$fail" | tee -a out/tests_summary.txt
  [ "$code" != "0" ] && failed=$((failed+1))
done
echo "FAILED_SUITES=$failed" | tee -a out/tests_summary.txt

echo "--- точность поиска по фото"
python tools/fetch_eval_images.py eval_images > out/eval_fetch.log 2>&1
python -m tools.eval_photo_search --images eval_images --provider local --json out/eval_local.json > out/eval_local.log 2>&1
tail -1 out/eval_local.log
python -m tools.eval_photo_search --images eval_images --provider clip --json out/eval_clip.json > out/eval_clip.log 2>&1
tail -1 out/eval_clip.log

echo "--- нагрузка"
for n in 1000 5000 10000; do
  python -m tools.bench_scale --items $n --photos 400 --ui --json out/bench_$n.json > out/bench_$n.log 2>&1
  tail -1 out/bench_$n.log
done

echo "--- снимки экранов"
python -u -m tools.render_ui --out out/renders --scale 1.0 > out/render_light.log 2>&1
python -u -m tools.render_ui --out out/renders --scale 1.0 --theme dark --only "dashboard,catalog,rentals,settings" > out/render_dark.log 2>&1
tail -1 out/render_light.log
exit 0
