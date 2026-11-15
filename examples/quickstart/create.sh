#!/bin/sh
set -eu

repo=${1:?Usage: create.sh /path/to/new-demo-repository}
mkdir "$repo"
git init -q "$repo"
git -C "$repo" config user.name "Mentat Quickstart"
git -C "$repo" config user.email "quickstart@example.invalid"
mkdir "$repo/.mentat"
cat > "$repo/.mentat/contract.json" <<'JSON'
{
  "version": 1,
  "claims": [
    {
      "id": "clamp",
      "statement": "Map inputs to 0 through 2 and preserve inputs already in range.",
      "assumptions": ["Inputs are integers from -3 through 3."]
    }
  ],
  "formal": [
    {
      "id": "clamp-model",
      "claim": "clamp",
      "engine": "z3",
      "source": "clamp.smt2",
      "expected": "unsat",
      "counterexample": "counterexample.smt2"
    }
  ],
  "behavior": [
    {
      "id": "clamp-cli",
      "claim": "clamp",
      "argv": ["/bin/sh", "{checks}/check.sh"],
      "artifacts": ["counterexample.txt"]
    }
  ],
  "docker_image": "alpine@sha256:4b7ce07002c69e8f3d704a9c5d6fd3053be500b7f1c69fc0d80990c2ad8dd412",
  "allow_host": true
}
JSON
cat > "$repo/.mentat/clamp.smt2" <<'SMT'
(declare-const x Int)
(assert (and (<= (- 3) x) (<= x 3)))
(define-fun clamp ((n Int)) Int (ite (< n 0) 0 (ite (> n 2) 2 n)))
SMT
cat > "$repo/.mentat/counterexample.smt2" <<'SMT'
(assert (or
  (and (< x 0) (distinct (clamp x) 0))
  (and (<= 0 x) (<= x 2) (distinct (clamp x) x))
  (and (> x 2) (distinct (clamp x) 2))))
SMT
cat > "$repo/.mentat/check.sh" <<'SH_CHECK'
set -eu
for input in -3 -2 -1 0 1 2 3; do
    case "$input" in
        -*) expected=0 ;;
        3) expected=2 ;;
        *) expected=$input ;;
    esac
    actual=$(./clamp "$input") || exit 2
    if [ "$actual" != "$expected" ]; then
        printf 'clamp %s returned %s, expected %s\n' "$input" "$actual" "$expected" |
            tee "$MENTAT_ARTIFACTS_DIR/counterexample.txt"
        exit 1
    fi
done
SH_CHECK

write_candidate() {
    cat > "$repo/clamp" <<SH_CLAMP
#!/bin/sh
set -eu
if [ "\$1" -lt 0 ]; then
    printf '%s\\n' '$1'
elif [ "\$1" -gt 2 ]; then
    printf '%s\\n' 2
else
    printf '%s\\n' "\$1"
fi
SH_CLAMP
    chmod +x "$repo/clamp"
}

write_candidate -1
git -C "$repo" add .
git -C "$repo" -c commit.gpgsign=false commit -qm "Add contract and buggy clamp"
git -C "$repo" tag mentat-demo-base
write_candidate 0
git -C "$repo" add clamp
git -C "$repo" -c commit.gpgsign=false commit -qm "Clamp negative values to zero"
git -C "$repo" tag mentat-demo-fixed
