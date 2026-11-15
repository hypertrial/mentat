import Main

example : ∀ (snapshot : List Entry) (path : List String),
    (snapshot.map Prod.fst).Nodup →
    lookup path (restore snapshot) = lookup path snapshot := restore_lookup

#print axioms restore_lookup
