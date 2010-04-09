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

import threading
from spark.async import BlockingQueue

__all__ = ["all", "current", "run_main", "spawn", "send", "receive"]

_lock = threading.RLock()
_processes = {}
_nextID = 0
_current = threading.local()

def all():
    """ Enumerates the IDs of all processes. """
    with _lock:
        return _processes.iter_keys()

def current():
    """ Return the ID of the currently executing process. """
    try:
        return _current.pid
    except AttributeError:
        return None

def attach(name=None):
    """ Associate the current thread with a PID and message queue. Return the PID. """
    current_pid = current()
    if current_pid is not None:
        raise Exception("This thread already has a PID")
    with _lock:
        pid = _new_id()
        p = _create_process(pid, name)
        _set_current_process(pid)
        p.thread = threading.current_thread()
    return pid

def detach():
    """ Disassociate the current thread from its PID and message queue. """
    current_pid = current()
    if current_pid is None:
        raise Exception("The current thread has no PID")
    with _lock:
        _remove_current_process(current_pid)

def spawn(fun, *args, **kargs):
    """ Create a new process and return its PID. """
    with _lock:
        pid = _new_id()
        p = _create_process(pid, None)
    def entry():
        with _lock:
            _set_current_process(pid)
        try:
            fun(*args, **kargs)
        finally:
            with _lock:
                _remove_current_process(pid)
    p.thread = threading.Thread(target=entry)
    p.thread.daemon = False
    p.thread.start()
    return pid

def send(pid, m):
    """ Send a message to the specified process. """
    with _lock:
        try:
            p = _processes[pid]
            queue = p.queue
        except KeyError:
            raise Exception("Invalid PID")
    queue.put(m)

def receive():
    """ Retrieve a message from the current process' queue. """
    try:
        queue = _current.queue
    except NameError:
        raise Exception("The current thread has no PID")
    return queue.get()

def _new_id():
    global _nextID
    id = _nextID
    _nextID += 1
    return id

def _create_process(pid, name):
    p = Process(pid, name)
    _processes[pid] = p
    return p

def _set_current_process(pid):
    p = _processes[pid]
    _current.pid = pid
    _current.queue = p.queue

def _remove_current_process(current_pid):
    queue = _current.queue
    queue.close()
    _current.pid = None
    _current.queue = None
    del _processes[current_pid]

class Process(object):
    def __init__(self, pid, name):
        self.pid = pid
        self.name = name
        self.queue = BlockingQueue(64)
        self.thread = None