(define (domain cluster-maintenance)
  (:requirements :strips :typing)
  (:types node)
  (:predicates
    (serving ?n - node)
    (drained ?n - node)
    (upgraded ?n - node)
  )
  (:action drain
    :parameters (?n - node)
    :precondition (serving ?n)
    :effect (and (not (serving ?n)) (drained ?n)))
  (:action upgrade
    :parameters (?n - node)
    :precondition (drained ?n)
    :effect (upgraded ?n))
  (:action bring-online
    :parameters (?n - node)
    :precondition (drained ?n)
    :effect (serving ?n))
)
