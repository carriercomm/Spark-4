# -*- coding: utf-8 -*-
#
# Copyright (C) 2010 Pierre-André Saulais <pasaulais@free.fr>
#
# This file is part of the Spark File-transfer Tool.
#
# Spark is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Spark is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Spark; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

""" Interface that allows the use of Erlang-like processes that can send messages to each other. """

from collections import Sequence, Mapping
import threading
import logging
from spark.core.queue import BlockingQueue, QueueClosedError
from spark.core import debugger

__all__ = ["Process", "ProcessState", "ProcessBase", "ProcessExit", "ProcessExited", "ProcessKilled",
           "ProcessNotifier", "Command", "Event", "EventSender", "match", "PatternMatcher"]

class Process(object):
    """ A process can execute callables and communicate using messages. """
    _lock = threading.RLock()
    _processes = {}
    _nextID = 1
    _current = threading.local()
    
    def __init__(self, pid, name):
        self.pid = pid
        self.name = name
        self.queue = BlockingQueue(64)
        self.thread = None
        self.state = None
        self.logger = None
        self.linked = set()
        self.trapExit = False
    
    def displayName(self):
        if self.name:
            return "%s-%i" % (self.name, self.pid)
        else:
            return "process-%i" % self.pid

    @classmethod
    def all(cls):
        """ Enumerates the IDs of all processes. """
        with cls._lock:
            return cls._processes.iter_keys()
    
    @classmethod
    def current(cls):
        """ Return the ID of the currently executing process. """
        try:
            return cls._current.p.pid
        except AttributeError:
            return None
    
    @classmethod
    def logger(cls):
        """ Return a logger for the currently executing process. """
        if hasattr(cls._current, "p"):
            p = cls._current.p
            if p.logger is None:
                p.logger = logging.getLogger(p.displayName())
            return p.logger
        else:
            return logging.getLogger()
    
    @classmethod
    def attach(cls, name=None, queue=None):
        """ Associate the current thread with a PID and message queue. Return the PID. """
        current_pid = cls.current()
        if current_pid is not None:
            raise Exception("This thread already has a PID")
        with cls._lock:
            pid = cls._new_id()
            p = cls._create_process(pid, name)
            if queue is not None:
                p.queue = queue
            cls._set_current_process(pid)
            p.thread = threading.current_thread()
        log = cls.logger()
        log.info("Process attached.")
        return pid
    
    @classmethod
    def detach(cls):
        """ Disassociate the current thread from its PID and message queue. """
        current_pid = cls.current()
        if current_pid is None:
            raise Exception("The current thread has no PID")
        log = cls.logger()
        log.info("Process detached.")
        with cls._lock:
            cls._remove_current_process(current_pid)
    
    @classmethod
    def spawn(cls, fun, args=(), name=None):
        """ Create a new process and return its PID. """
        return cls._spawn(fun, args, name, None)
    
    @classmethod
    def spawn_linked(cls, fun, args=(), name=None):
        """ Create a new process linked to the current one and return its PID.
        When a process dies, every process in its link set is killed. """
        currentPid = cls.current()
        if not currentPid:
            raise Exception("The current thread has no PID")
        return cls._spawn(fun, args, name, currentPid)
    
    @classmethod
    def trap_exit(cls):
        """ Change the current process to trap exits. If any process in its
        link set dies, this process will be sent an Exit message instead of being killed. """
        currentPid = cls.current()
        if not currentPid:
            raise Exception("The current thread has no PID")
        with cls._lock:
            cls._processes[currentPid].trapExit = True
    
    @classmethod
    def _spawn(cls, fun, args=(), name=None, linkedPid=None):
        with cls._lock:
            pid = cls._new_id()
            p = cls._create_process(pid, name)
            if linkedPid:
                p.linked.add(linkedPid)
                cls._processes[linkedPid].linked.add(pid)
        p.thread = threading.Thread(target=cls._entry,
            name=p.displayName(), args=(pid, fun, args))
        #p.thread.daemon = True
        p.thread.start()
        return pid
    
    @classmethod
    def _entry(cls, pid, fun, args):
        with cls._lock:
            cls._set_current_process(pid)
            p = cls._current.p
        log = cls.logger()
        log.info("Process started.")
        try:
            fun(*args)
            gracefulExit = True
            exitReason = None
        except ProcessExit as e:
            exitReason = e.reason
            if exitReason is None:
                gracefulExit = True
            else:
                log.error("Process exited with reason %s." % repr(e.reason))
                gracefulExit = False
        except ProcessKilled:
            gracefulExit = False
            exitReason = "killed"
        except NoMatchException as nme:
            log.error(str(nme))
            gracefulExit = False
            exitReason = "no-match"
        except Exception:
            log.exception("An exception was raised by the process")
            gracefulExit = False
            exitReason = "exception"
        finally:
            with cls._lock:
                if not gracefulExit:
                    log.error("Process died.")
                else:
                    log.info("Process stopped.")
                # notify/kill linked processes
                for linkedPid in p.linked:
                    linkedProcess = cls._processes[linkedPid]
                    if linkedProcess.trapExit:
                        Process.try_send(linkedPid, Event("exit", pid, exitReason))
                    elif not gracefulExit and linkedProcess.queue.close():
                        log.error("Killing linked process %d.", linkedPid)
                cls._remove_current_process(pid)
    
    @classmethod
    def _getQueue(cls, pid):
        """ Return the queue of the specified process. """
        with cls._lock:
            try:
                p = cls._processes[pid]
            except KeyError:
                raise Exception("Invalid PID")
        return p.queue
    
    @classmethod
    def _getQueueCached(cls, pid):
        """ Return the queue of the specified process, using the cache if possible. """
        if not hasattr(cls._current, "queues"):
            cls._current.queues = {}
        try:
            return cls._current.queues[pid]
        except KeyError:
            queue = cls._getQueue(pid)
            cls._current.queues[pid] = queue
            return queue
    
    @classmethod
    def send(cls, pid, m):
        """ Send a message to the specified process. """
        if not cls._current.p.queue.isOpen:
            raise ProcessKilled()
        pid = cls._to_pid(pid)
        queue = cls._getQueue(pid)
        try:
            queue.put(m)
        except QueueClosedError:
            raise ProcessExited("Can't send a message to a stopped process (PID: %i)" % pid)
    
    @classmethod
    def try_send(cls, pid, m):
        """ Send a message to the specified process. If the process exited, return False. """
        pid = cls._to_pid(pid)
        try:
            cls.send(pid, m)
            return True
        except ProcessExited:
            return False
    
    @classmethod
    def receive(cls):
        """ Retrieve a message from the current process' queue. """
        try:
            p = cls._current.p
        except NameError:
            raise Exception("The current thread has no PID")
        try:
            return p.queue.get()
        except QueueClosedError:
            raise ProcessKilled("The process got killed (PID: %i)" % p.pid)
    
    @classmethod
    def try_receive(cls):
        """
        Retrieve a message from the current process' queue and return (True, message).
        If the queue is empty, return (False, None).
        """
        try:
            p = cls._current.p
        except NameError:
            raise Exception("The current thread has no PID")
        try:
            return p.queue.get_unless_empty()
        except QueueClosedError:
            raise ProcessKilled("The process got killed (PID: %i)" % p.pid)
    
    @classmethod
    def kill(cls, pid, flushQueue=True):
        """ Kill the specified process by closing its message queue. Return False on error. """
        pid = cls._to_pid(pid)
        with cls._lock:
            try:
                p = cls._processes[pid]
                queue = p.queue
            except KeyError:
                return False
        queue.close(flushQueue)
        return True
    
    @classmethod
    def exit(cls, reason=None):
        """ Stop the current process. This raises the ProcessExit exception.
        If reason is None the exit is graceful.
        To indicate an error pass a reason other than None."""
        raise ProcessExit(reason)
    
    @classmethod
    def _to_pid(cls, pid):
        if hasattr(pid, "pid"):
            return pid.pid
        elif type(pid) is int:
            return pid
        else:
            raise Exception("Invalid PID '%s'" % repr(pid))
    
    @classmethod
    def _new_id(cls):
        id = cls._nextID
        cls._nextID += 1
        return id
    
    @classmethod
    def _create_process(cls, pid, name):
        p = cls(pid, name)
        cls._processes[pid] = p
        return p
    
    @classmethod
    def _set_current_process(cls, pid):
        cls._current.p = cls._processes[pid]
    
    @classmethod
    def _set_state(cls, state):
        cls._current.p.state = state
    
    @classmethod
    def _remove_current_process(cls, current_pid):
        p = cls._current.p
        p.queue.close()
        del cls._current.p
        #del cls._processes[current_pid]

class ProcessExit(Exception):
    """ Exception raised when a process has to stop executing.
    If reason is None the exit is graceful.
    To indicate an error pass a reason other than None."""
    def __init__(self, reason=None):
        super(Exception, self).__init__()
        self.reason = reason

class ProcessExited(Exception):
    pass

class ProcessKilled(Exception):
    pass

class ProcessState(object):
    """ Object that can be used to store a process' state (which should not be shared across threads). """
    pass

class ProcessNotifier(object):
    """ Notifies other processes by sending messages. """
    def __init__(self, lock=None):
        self.__lock = lock or threading.Lock()
        self.__suscribers = set()
    
    def suscribe(self, pid=None):
        """ Suscribe a process to start receiving notifications. """
        if not pid:
            pid = Process.current()
            if not pid:
                raise Exception("The current thread has no PID")
        with self.__lock:
            self.__suscribers.add(pid)
    
    def unsuscribe(self, pid=None):
        """ Unsuscribe a process to stop receiving notifications. """
        if not pid:
            pid = Process.current()
            if not pid:
                raise Exception("The current thread has no PID")
        with self.__lock:
            self.__suscribers.remove(pid)

    def __call__(self, m):
        """ Send a message to all suscribed processes. """
        # don't send messages with the lock held, so need to copy the suscribers
        with self.__lock:
            suscribers = self.__suscribers.copy()
        for pid in suscribers:
            try:
                Process.send(pid, m)
            except Exception:
                pass

class ProcessMessage(Sequence):
    """ Base class for messages that can be sent to a process."""
    def __init__(self, name, *params):
        self.type = self.__class__.__name__
        self.name = name
        self.params = params
    
    def __len__(self):
        return 2 + len(self.params)
    
    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self)[index]
        elif index == 0:
            return self.type
        elif index == 1:
            return self.name
        elif (index > 1) and (index < (len(self.params) + 2)):
            return self.params[index - 2]
        else:
            raise IndexError("Index '%i' out of range" % index)
    
    def __str__(self):
        return str(self[:])
    
    def __repr__(self):
        return repr(self[:])

class Command(ProcessMessage):
    """ Contains information about a command sent to a process."""
    pass

class Event(ProcessMessage):
    """ Contains information about an event sent by a process. """
    pass

def match(pattern, o):
    """ Try to match an object against a pattern. Return True if the pattern is matched or False otherwise. """
    if (pattern is None) or (pattern == o):
        return True
    elif isinstance(pattern, type):
        # match types
        return isinstance(o, pattern) or (o is None)
    elif isinstance(pattern, basestring):
        # match strings
        return pattern == o
    elif isinstance(pattern, Mapping):
        # match dicts
        if isinstance(o, Mapping):
            for key in pattern:
                if (not key in o) or (not match(pattern[key], o[key])):
                    return False
            return True
        else:
            return False
    elif isinstance(pattern, Sequence):
        # match lists
        if isinstance(o, Sequence):
            n = len(pattern)
            if n != len(o):
                return False
            else:
                for i in range(0, n):
                    if not match(pattern[i], o[i]):
                        return False
                return True
        else:
            return False
    else:
        return False

class EventSender(ProcessNotifier):
    """
    Event which can be suscribed by other processes.
    Example:
        EventSender("protocol-negociated", basestring) can send events such as
        Event("protocol-negociated", "SPARKv1") but not
        Event("protocol-negociated") or even
        Event("connected", "127.0.0.1:4550").
    """
    def __init__(self, name, *args):
        super(EventSender, self).__init__()
        self.pattern = Event(name, *args)
    
    def suscribe(self, pid=None):
        """ Suscribe a process to start receiving events and return its pattern. """
        super(EventSender, self).suscribe(pid)
        return self.pattern
    
    def __call__(self, *args):
        """ Send a notification to all suscribed processes. """
        event = Event(self.pattern.name, *args)
        if match(self.pattern, event):
            super(EventSender, self).__call__(event)
        else:
            raise TypeError("%s doesn't match the pattern %s" %
                            (repr(event), repr(self.pattern)))

class NoMatchException(Exception):
    """ Error that occurs when MessageMatcher.match() is called and no match was found. """
    pass

def toPascalCase(tag):
    """ Convert the tag to Pascal case (e.g. "create-transfer" becomes "CreateTransfer"). """
    return "".join([word.capitalize() for word in tag.split("-")])

class PatternMatcher(object):
    """ Matches messages against a list of patterns. """
    def __init__(self):
        self.patterns = []
        self.predicates = []
    
    def addPattern(self, pattern, callable=None, result=True):
        """ Add a pattern to match messages. """
        self.patterns.append((pattern, callable, result))
    
    def removePattern(self, pattern, callable=None, result=True):
        """ Remove a pattern from the list. """
        self.patterns.remove((pattern, callable, result))
    
    def addHandlers(self, handler, *patterns):
        """ Calls addHandler() for every pattern in the list. """
        for pattern in patterns:
            self.addHandler(pattern, handler)
    
    def addHandler(self, pattern, handler, result=True, prefixes={"Event": "on", "Command": "do"}):
        """
        Add a rule that invokes the relevant handler methods when a message is matched.
        For example a 'connect' command would invoke the 'doCommand' method (with the default prefixes).
        """
        if match((basestring, basestring), pattern[0:2]):
            name = pattern.__class__.__name__
            if name in prefixes:
                prefix = prefixes[name]
            else:
                prefix = name.lower()
            attrName = prefix + toPascalCase(pattern[1])
            def invokeHandler(m, *args):
                attr = getattr(handler, attrName, None)
                if hasattr(attr, "__call__"):
                    attr(m, *(m[2:] + args))
                else:
                    raise AttributeError("Could not find handler method '%s' for message %s"
                        % (attrName, repr(m)))
            self.addPattern(pattern, invokeHandler, result)
        else:
            raise TypeError("pattern should be a message (sequence starting with two strings)")
    
    def suscribeTo(self, sender, callable=None, result=True):
        """ Suscribe to an event after adding its pattern to the list. """
        self.addPattern(sender.pattern, callable, result)
        sender.suscribe()
    
    def match(self, m, *args):
        """ Match the message against the patterns. """
        for pattern, callable, result in reversed(self.patterns):
            if match(pattern, m):
                if callable:
                    callable(m, *args)
                return result
        error = ["No pattern matched message %s.\nPossible patterns:" % repr(m)]
        for i, (pattern, callable, result) in enumerate(reversed(self.patterns)):
            error.append("%2d: %s" % (i, repr(pattern)))
        raise NoMatchException("\n".join(error))
    
    def run(self, *args):
        """ Retrieve messages from the current process' queue while they match any pattern. """
        while True:
            m = Process.receive()
            if not self.match(m, *args):
                break

class ProcessBase(object):
    """ Base class for processes with a message loop. """
    def __init__(self, name=None):
        self.pid = None
        if name:
            self.name = name
        else:
            self.name = self.__class__.__name__
    
    def start(self):
        """ Start the new process if it is not already running. """
        if not self.pid:
            self.pid = Process.spawn(self.run, name=self.name)
        return self.pid
    
    def start_linked(self):
        """ Start the new process as a linked process if it is not already running. """
        if not self.pid:
            self.pid = Process.spawn_linked(self.run, name=self.name)
        return self.pid
    
    def attach(self, queue=None):
        """ Create a process that is attached to the current thread and run it. """
        if not self.pid:
            try:
                self.pid = Process.attach(self.name, queue)
                self.run()
            except ProcessKilled:
                Process.logger().error("Process killed.")
            finally:
                Process.detach()
                self.pid = None
    
    def stop(self):
        """ Stop the process if it is running. """
        if self.pid:
            try:
                Process.try_send(self.pid, Command("stop"))
            except ProcessKilled:
                Process.kill(self.pid)
            self.pid = None
    
    def initState(self, state):
        """ Initialize the process state. """
        state.logger = Process.logger()

    def initPatterns(self, loop, state):
        """ Initialize the patterns used by the message loop. """
        loop.addPattern(Command("stop"), result=False)
        if debugger.enabled():
            loop.addPattern(Command("rpdb"), debugger.launch_remote)
            loop.addPattern(Command("pdb"), debugger.launch_local)
    
    def onStart(self, state):
        """ Called just before the process enters its message loop. """
        pass
    
    def handleMessage(self, message, state):
        """ Handle a message that was sent to the process. """
        if not state.matcher.match(message, state):
            Process.exit()
    
    def run(self):
        """ Run the process. This method blocks until the process has finished executing. """
        state = ProcessState()
        #HACK: make this better at some point
        Process._set_state(state)
        self.initState(state)
        try:
            state.matcher = PatternMatcher()
            self.initPatterns(state.matcher, state)
            self.onStart(state)
            while True:
                m = Process.receive()
                self.handleMessage(m, state)
        finally:
            self.cleanup(state)
    
    def cleanup(self, state):
        """ Perform cleanup tasks before the process stops.
        This is guaranteed to be called if the process state was initialized properly. """
        pass
    
    def __enter__(self):
        """ Start the new process if it is not already running. """
        self.start_linked()
        return self
    
    def __exit__(self, type, val, tb):
        """ Stop the process if it is running. """
        self.stop()