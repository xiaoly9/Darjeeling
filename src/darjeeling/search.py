from timeit import default_timer as timer
from typing import Optional
import threading
import random
import datetime

import bugzoo

from darjeeling.candidate import Candidate
from darjeeling.problem import Problem


class Worker(threading.Thread):
    def __init__(self, master):
        super().__init__()
        self.daemon = True
        self.__master = master
        self.start()

    @property
    def master(self):
        return self.__master

    def run(self):
        while not self.master.halted:
            candidate = self.master.next()
            if not candidate:
                break
            self.master.evaluate(candidate)


class RandomSearch(object):
    def __init__(self,
                 bugzoo: bugzoo.BugZoo,
                 problem: Problem,
                 num_threads: int,
                 terminate_early: Optional[bool] = True,
                 time_limit: Optional[datetime.timedelta] = None
                 ):
        assert num_threads > 0
        self.halted = False
        self.__time_start = None
        self.__bugzoo = bugzoo
        self.__lock = threading.Lock()
        self.__problem = problem
        self.__num_threads = num_threads
        self.__terminate_early = terminate_early
        self.__time_limit = time_limit

        candidates = {}
        for t in problem.transformations:
            candidate = Candidate([t])
            if t.line not in candidates:
                candidates[t.line] = []
            candidates[t.line].append(candidate)

        # shuffle the candidates at each line
        for line_candidates in candidates.values():
            random.shuffle(line_candidates)

        self.__candidates = candidates

    @property
    def terminate_early(self) -> bool:
        """
        A flag that specifies whether or not the search should terminate upon
        discovering an acceptable patch. If set to `True`, the search will end
        after an acceptable patch has been discovered; if set to `False`, the
        search will continue until the termination criteria of the search have
        been hit or the search space has been exhausted.
        """
        return self.__terminate_early

    @property
    def num_threads(self) -> int:
        """
        The number of threads that are available to this search algorithm.
        """
        return self.__num_threads

    @property
    def problem(self) -> Problem:
        """
        A description of the problem that is being solved by this search
        algorithm.
        """
        return self.__problem

    @property
    def time_limit(self) -> Optional[datetime.timedelta]:
        """
        An optional time limit that is imposed upon the search process.
        """
        return self.__time_limit

    @property
    def time_running(self) -> datetime.timedelta:
        """
        The length of time that has passed since the search begun.
        """
        if not self.__running:
            raise Exception("search hasn't started")

        return timer() - self.__time_start

    def run(self, seed: Optional[int] = None) -> None:
        if seed is None: # TODO: should be equiv?
            random.seed()
        else:
            random.seed(seed)

        self.__time_start = timer()
        workers = [Worker(self) for _ in range(self.num_threads)]
        for worker in workers:
            worker.join()

    def next(self) -> Optional[Candidate]:
        self.__lock.acquire()
        try:
            implicated_lines = list(self.__candidates.keys())

            if self.time_limit and self.time_running > self.time_limit:
                print("Reached time limit.")
                self.halted = True
                return None

            # have all candidates been exhausted?
            if not implicated_lines:
                print("Exhausted search space.")
                self.halted = True
                return None

            # choose a line at random
            # TODO: use fault localisation
            line = random.choice(implicated_lines)

            candidates = self.__candidates[line]
            assert candidates is not []
            candidate = candidates.pop()

            # remove line from search space when we've exhausted all of its
            # candidate patches
            if candidates == []:
                del self.__candidates[line]

            return candidate

        finally:
            self.__lock.release()

    def evaluate(self, candidate: Candidate) -> None:
        print("Evaluating: {}".format(candidate))
        container = None
        try:
            container = self.__bugzoo.container.provision(self.problem.bug)
            patch = candidate.diff(self.problem)

            container.patch(patch)

            # ensure that the patch compiles
            if not container.compile().successful:
                return

            # for now, execute all tests in no particular order
            for test in self.problem.tests:
                outcome = container.execute(test)
                if not outcome.passed:
                    return

            # if we've found a repair, halt the search
            diff = candidate.diff(self.problem)

            # how long did it take to find a repair?
            t = (timer() - self.__time_start).total_seconds()
            t /= 60.0

            print("FOUND REPAIR [{:.2f} minutes]: {}\n{}\n{}\n{}".format(t, candidate,
                                                        ("=" * 80),
                                                        diff,
                                                        ("="*80)))
            if self.terminate_early:
                print("Terminating search")
                self.halted = True
        finally:
            if container:
                container.destroy()