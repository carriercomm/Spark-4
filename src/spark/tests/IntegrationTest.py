#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2009 Pierre-André Saulais <pasaulais@free.fr>
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

import unittest
import threading
import functools
import time
from spark.async import Future, coroutine, Process, ProcessRunner
from spark.messaging.messages import *
from spark.messaging.service import TcpMessenger, Service
from spark.tests.common import run_tests, processTimeout

BIND_ADDRESS = "127.0.0.1"
BIND_PORT = 4550

class TestServer(Service):
    def __init__(self):
        super(TestServer, self).__init__()
        self.listening = Event("listening")
    
    def initPatterns(self, loop, state):
        super(TestServer, self).initPatterns(loop, state)
        state.messenger.listening.suscribe(matcher=loop, callable=self.handleListening)
        state.messenger.disconnected.suscribe(matcher=loop, result=False)
        loop.addPattern(Request("swap", (None, None)), self.handleSwap)
    
    def handleListening(self, m, state):
        self.listening(m)
    
    def handleSwap(self, req, state):
        self.sendResponse(req, state, (req.params[1], req.params[0]))
    
class ProcessIntegrationTest(unittest.TestCase):
    def assertMatch(self, pattern, o):
        if not match(pattern, o):
            self.fail("Object doesn't match the pattern: '%s' (pattern: '%s')"
                % (repr(o), repr(pattern)))
    
    @processTimeout(1.0)
    def testTcpSession(self):
        pid = Process.current()
        server = TestServer()
        runner = ProcessRunner(server)
        runner.start()
        server.listening.suscribe()
        Process.send(runner.pid, ("bind", (BIND_ADDRESS, BIND_PORT)))
        self.assertMatch(Notification("listening", None), Process.receive())
        clientMessenger = TcpMessenger()
        clientMessenger.protocolNegociated.suscribe()
        clientMessenger.connect((BIND_ADDRESS, BIND_PORT))
        self.assertMatch(Notification("protocol-negociated", None), Process.receive())
        clientMessenger.send(Request("swap", ("foo", "bar"), 1))
        self.assertMatch(Response("swap", ("bar", "foo"), 1), Process.receive())
        clientMessenger.close()

if __name__ == '__main__':
    import logging
    run_tests(level=logging.INFO)