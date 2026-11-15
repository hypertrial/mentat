import Std

abbrev Entry := List String × List (String × String)

-- In the model, restore enumerates a clean destination in insertion-stack order.
def restore (snapshot : List Entry) : List Entry := snapshot.reverse

def lookup (path : List String) (entries : List Entry) : Option Entry :=
  entries.find? (fun entry => decide (entry.1 = path))

theorem restore_lookup (snapshot : List Entry) (path : List String)
    (distinct : (snapshot.map Prod.fst).Nodup) :
    lookup path (restore snapshot) = lookup path snapshot := by
  induction snapshot with
  | nil => rfl
  | cons entry rest ih =>
    have h := List.nodup_cons.mp (show (entry.1 :: rest.map Prod.fst).Nodup from distinct)
    have tail : rest.reverse.find? (fun item => decide (item.1 = path)) =
        rest.find? (fun item => decide (item.1 = path)) := ih h.2
    simp only [restore, lookup, List.reverse_cons, List.find?_append, tail, List.find?_cons,
      List.find?_nil]
    by_cases same : entry.1 = path
    · have absent : rest.find? (fun item => decide (item.1 = path)) = none := by
        apply List.find?_eq_none.mpr
        intro item member
        have ne : item.1 ≠ path := by
          intro eq
          apply h.1
          rw [same, ← eq]
          exact List.mem_map_of_mem member
        simp [ne]
      simp [same, absent]
    · simp [same]
