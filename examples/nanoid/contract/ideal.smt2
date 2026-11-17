; Abstract rule for a parsed number; this does not model the CLI implementation.
(declare-const finite Bool)
(declare-const x Real)
(define-fun accepted () Bool
  (and finite (> x 0) (= x (to_real (to_int x)))))
