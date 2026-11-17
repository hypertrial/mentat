import Std

namespace IcebergCompaction

-- Counts are multiplicities of an arbitrary row, not membership in a set.
theorem rows_preserved (source selected replacement published : Nat)
    (_bounded : source ≤ 12)
    (whole_file : selected = source)
    (copies_selected : replacement = selected)
    (atomic_publication : published = replacement) :
    published = source := by
  exact atomic_publication.trans (copies_selected.trans whole_file)

end IcebergCompaction

namespace IcebergCompaction

-- Each edge is one complete atomic publication; an empty chain is a safe prefix.
inductive Committed : Nat → Nat → Prop
  | initial (rows : Nat) : Committed rows rows
  | next {initial source selected replacement published : Nat} :
      Committed initial source → source ≤ 12 → selected = source →
      replacement = selected → published = replacement → Committed initial published

theorem committed_prefixes_preserved {initial current : Nat}
    (history : Committed initial current) : current = initial := by
  induction history with
  | initial => rfl
  | next _ bounded whole copies atomic ih =>
    exact (rows_preserved _ _ _ _ bounded whole copies atomic).trans ih

end IcebergCompaction
