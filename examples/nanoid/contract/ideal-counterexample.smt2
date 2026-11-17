(assert (or
  (and (not finite) accepted)
  (and finite (<= x 0) accepted)
  (and finite (= x (/ 1 2)) accepted)
  (and finite (= x (/ 3 2)) accepted)
  (and finite (= x 7) (not accepted))))
