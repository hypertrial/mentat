; Bounded eligibility model: each input represents an independent gate.
(declare-const status Int)
(declare-const approval-current Bool)
(declare-const evidence-intact Bool)
(declare-const operator-confirmed Bool)
(declare-const accepted Bool)
(assert (and (<= 0 status) (<= status 2)))
(assert (= accepted (and (= status 0) approval-current evidence-intact operator-confirmed)))
