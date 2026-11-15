; Values are half-units; types 0, 1, 2 denote integer, float, double.
(declare-const n Int)
(declare-const t0 Int)
(declare-const t1 Int)
(declare-const t2 Int)
(declare-const v0 Int)
(declare-const v1 Int)
(declare-const v2 Int)
(assert (and (<= 1 n) (<= n 3)))
(assert (and (<= 0 t0) (<= t0 2) (<= 0 t1) (<= t1 2) (<= 0 t2) (<= t2 2)))
(assert (and (<= (- 20) v0) (<= v0 20) (<= (- 20) v1) (<= v1 20) (<= (- 20) v2) (<= v2 20)))
(assert (and (=> (= t0 0) (= (mod v0 2) 0)) (=> (= t1 0) (= (mod v1 2) 0)) (=> (= t2 0) (= (mod v2 2) 0))))
(assert (and (=> (> n 1) (or (distinct t0 t1) (distinct v0 v1))) (=> (> n 2) (and (or (distinct t0 t2) (distinct v0 v2)) (or (distinct t1 t2) (distinct v1 v2))))))
(define-fun reference () Int (+ v0 (ite (> n 1) v1 0) (ite (> n 2) v2 0)))
(define-fun reference-type () Int
  (ite (or (and (> n 1) (distinct t0 t1)) (and (> n 2) (distinct t0 t2))) 2 t0))

(define-fun present ((kind Int)) Bool
  (or (= t0 kind) (and (> n 1) (= t1 kind)) (and (> n 2) (= t2 kind))))
(define-fun subtotal ((kind Int)) Int
  (+ (ite (= t0 kind) v0 0)
     (ite (and (> n 1) (= t1 kind)) v1 0)
     (ite (and (> n 2) (= t2 kind)) v2 0)))
(define-fun promotes () Bool (or (present 2) (and (present 0) (present 1))))
(define-fun corrected-type () Int (ite promotes 2 (ite (present 0) 0 1)))
(define-fun corrected () Int
  (ite promotes (+ (subtotal 0) (subtotal 1) (subtotal 2))
    (ite (present 0) (subtotal 0) (subtotal 1))))
