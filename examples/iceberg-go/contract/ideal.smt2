; Multiplicities of one arbitrary row in the bounded twelve-row fixture.
(declare-const source Int)
(declare-const selected Int)
(declare-const replacement Int)
(declare-const published Int)
(assert (and (<= 0 source) (<= source 12)))
(assert (and (<= 0 selected) (<= selected source)))
; Whole-file ownership, exact copying, and atomic replacement respectively.
(assert (= selected source))
(assert (= replacement selected))
(assert (= published replacement))
