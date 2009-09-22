/*
 Copyright (C) 2009 Pierre-André Saulais <pasaulais@free.fr>

 This file is part of the Spark File-transfer Tool.

 Spark is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.

 Spark is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with Spark; if not, write to the Free Software
 Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
*/

#include <stdio.h>
#include <malloc.h>
#include <unistd.h>
#include <libssh/server.h>

#include "common.h"
#include "server.h"

// Listen for incoming SSH connections.
// When a connection is established, write all data received to stdout.
void server_pipe(char *host, int port)
{
    SSH_OPTIONS *opt = ssh_options_new();
    ssh_options_set_host(opt, host);
    ssh_options_set_port(opt, port);
    ssh_options_set_rsa_server_key(opt, "test-server-key");
    
    SSH_BIND *b = ssh_bind_new();
    ssh_bind_set_options(b, opt);
    if(ssh_bind_listen(b) < 0)
        session_error(b, "listen");
    
    SSH_SESSION *s = ssh_bind_accept(b);
    if(!s)
        session_error(b, "accept");
    if(ssh_accept(s) < 0)
        session_error(s, "handshake");
    
    int state = SERVER_CONNECTED;
    while(1)
    {
        SSH_MESSAGE *m = ssh_message_get(s);
        if(m)
        {
            int type = ssh_message_type(m);
            int subtype = ssh_message_subtype(m);
            ssh_message_auth_set_methods(m, SSH_AUTH_METHOD_PUBLICKEY);
            server_handle_message(s, m, type, subtype, &state);
            ssh_message_free(m);
            if(state == SERVER_CLOSED)
            {
                ssh_disconnect(s);
                ssh_bind_free(b);
                ssh_finalize();
                return;
            }
        }
        else
        {
            session_error(s, "session");
        }
    }
}

void server_handle_message(SSH_SESSION *s, SSH_MESSAGE *m, int type, int subtype, int *state)
{
    int handled = 0;
    if((*state == SERVER_CONNECTED) && (type == SSH_REQUEST_AUTH) && (subtype == SSH_AUTH_METHOD_PUBLICKEY))
    {
        ssh_public_key key = ssh_message_auth_publickey(m);
        ssh_string keystr = publickey_to_string(key);
        char *keyhash = pubkey_hash(keystr);
        int has_sig = ssh_message_auth_sig_state(m);
        if(has_sig == 0)
        {
            if(authenticate(keyhash, 1))
            {
                //FIXME: type detection
                ssh_string algostr = string_from_char("ssh-rsa");
                ssh_message_auth_reply_pk_ok(m, algostr, keystr);
                handled = 1;
                string_free(algostr);
            }
        }
        else if(has_sig == 1)
        {
            if(authenticate(keyhash, 0))
            {
                session_event(s, "authenticated", keyhash);
                ssh_message_auth_reply_success(m, 0);
                handled = 1;
                *state = SERVER_AUTHENTICATED;
            }
            else
            {
                ssh_message_reply_default(m);
                handled = 1;
                *state = SERVER_CLOSED;
            }
        }
        string_free(keystr);
        free(keyhash);
    }
    else if((*state == SERVER_AUTHENTICATED) && (type == SSH_REQUEST_CHANNEL_OPEN) && (subtype == SSH_CHANNEL_SESSION))
    {
        ssh_channel chan = ssh_message_channel_request_open_reply_accept(m);
        if(!chan)
            session_error(s, "open-channel");
        handled = 1;
        session_event(s, "channel-opened", NULL);
        channel_to_file(chan, 1);
        channel_free(chan);
        *state = SERVER_CLOSED;
    }
    if(!handled)
        ssh_message_reply_default(m);
}
