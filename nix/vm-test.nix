{
  self,
  pkgs,
  # Declare the mailbox through configFile, a private JSON with the password inside,
  # rather than through the mailboxes option
  private ? false,
}:

# The real service, against a real Dovecot and Postfix on the same machine. Nothing here
# needs the outside network or a person to sign in, so unlike a stand-in this run covers
# the whole path: the unit's sandbox, its credentials, TLS checked against the system's
# trust, IMAP IDLE pushing a message to a watcher, and SMTP submission back out
let
  domain = "test.local";
  password = "agent-password-in-the-fixture";

  # A certificate for the name the service dials, trusted the way any CA would be. The
  # service checks it with no option of its own, as it does in production
  certificate = pkgs.runCommand "mail-test-certificate" { nativeBuildInputs = [ pkgs.openssl ]; } ''
    mkdir -p $out
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -subj "/CN=mail" -addext "subjectAltName=DNS:mail" \
      -keyout $out/key.pem -out $out/cert.pem
  '';

  users = pkgs.writeText "dovecot-users" ''
    agent@${domain}:{PLAIN}${password}::::::
    friend@${domain}:{PLAIN}friend::::::
    other@${domain}:{PLAIN}other::::::
  '';

  mailboxes = pkgs.writeText "vmailbox" ''
    agent@${domain} OK
    friend@${domain} OK
    other@${domain} OK
  '';

  # An alias of the agent's mailbox, the way a per-service address is one
  aliases = pkgs.writeText "virtual" ''
    alias-ci@${domain} agent@${domain}
  '';

  # A message with an attachment, sent as it is
  withAttachment = pkgs.writeText "with-attachment.eml" ''
    From: friend@${domain}
    To: agent@${domain}
    Subject: the report
    MIME-Version: 1.0
    Content-Type: multipart/mixed; boundary="b"

    --b
    Content-Type: text/plain

    see attached
    --b
    Content-Type: text/plain; name="report.txt"
    Content-Disposition: attachment; filename="report.txt"

    the attached text
    --b--
  '';

  queue = "/var/lib/postfix/queue/private";
in
pkgs.testers.runNixOSTest {
  name = if private then "mail-agent-private" else "mail-agent";

  nodes.mail = {
    imports = [ self.nixosModules.default ];

    security.pki.certificateFiles = [ "${certificate}/cert.pem" ];

    users = {
      users = {
        alice.isNormalUser = true;
        bob.isNormalUser = true;
        vmail = {
          isSystemUser = true;
          group = "vmail";
        };
      };
      groups.vmail = { };
    };

    systemd = {
      tmpfiles.rules = [ "d /var/vmail 0750 vmail vmail -" ];
      # Dovecot puts its sockets in Postfix's queue, which Postfix's setup creates
      services.dovecot = {
        after = [ "postfix-setup.service" ];
        wants = [ "postfix-setup.service" ];
      };
    };

    environment.systemPackages = with pkgs; [
      python3
      swaks
    ];

    services = {
      dovecot2 = {
        enable = true;
        settings = {
          dovecot_config_version = "2.4.4";
          dovecot_storage_version = "2.4.4";
          protocols = {
            imap = true;
            lmtp = true;
          };
          mail_driver = "maildir";
          mail_home = "/var/vmail/%{user | domain}/%{user | username}";
          mail_path = "~/Maildir";
          mail_uid = "vmail";
          mail_gid = "vmail";
          ssl = "required";
          ssl_server_cert_file = "${certificate}/cert.pem";
          ssl_server_key_file = "${certificate}/key.pem";
          auth_mechanisms = [ "plain" ];
          "passdb passwd-file".passwd_file_path = "${users}";
          "userdb passwd-file".passwd_file_path = "${users}";
          "namespace inbox" = {
            inbox = true;
            separator = "/";
            "mailbox Drafts" = {
              auto = "subscribe";
              special_use = "\\Drafts";
            };
            "mailbox Sent" = {
              auto = "subscribe";
              special_use = "\\Sent";
            };
            "mailbox Trash" = {
              auto = "subscribe";
              special_use = "\\Trash";
            };
          };
          service = [
            {
              _section.name = "imap-login";
              "inet_listener imap".port = 0;
            }
            {
              _section.name = "auth";
              "unix_listener ${queue}/auth" = {
                mode = "0660";
                user = "postfix";
                group = "postfix";
              };
            }
            {
              _section.name = "lmtp";
              "unix_listener ${queue}/dovecot-lmtp" = {
                mode = "0600";
                user = "postfix";
                group = "postfix";
              };
            }
          ];
        };
      };

      postfix = {
        enable = true;
        enableSubmission = false;
        enableSubmissions = false;
        settings.main = {
          myhostname = "mail";
          mydestination = "localhost";
          virtual_mailbox_domains = domain;
          virtual_mailbox_maps = "texthash:${mailboxes}";
          virtual_alias_maps = "texthash:${aliases}";
          virtual_transport = "lmtp:unix:private/dovecot-lmtp";
          smtpd_tls_cert_file = "${certificate}/cert.pem";
          smtpd_tls_key_file = "${certificate}/key.pem";
          smtpd_sasl_type = "dovecot";
          smtpd_sasl_path = "private/auth";
        };
        settings.master.smtps = {
          type = "inet";
          private = false;
          command = "smtpd";
          args = [
            "-o smtpd_tls_wrappermode=yes"
            "-o smtpd_sasl_auth_enable=yes"
            "-o smtpd_relay_restrictions=permit_sasl_authenticated,reject"
            "-o smtpd_client_restrictions=permit_sasl_authenticated,reject"
          ];
        };
      };

      mail-agent = {
        enable = true;
        socketUser = "alice";
      }
      // (
        if private then
          {
            configFile = pkgs.writeText "mail-agent.json" (
              builtins.toJSON {
                agent = {
                  address = "agent@${domain}";
                  imap_host = "mail";
                  inherit password;
                  own = [ "/alias-.*@test\\.local/" ];
                };
              }
            );
          }
        else
          {
            mailboxes.agent = {
              address = "agent@${domain}";
              passwordFile = pkgs.writeText "agent-password" password;
              imap.host = "mail";
              ownAddresses = [ "/alias-.*@test\\.local/" ];
            };
          }
      );
    };
  };

  # Paths come from the module, so a changed default is tested rather than bypassed
  testScript =
    { nodes, ... }:
    let
      inherit (nodes.mail.services.mail-agent)
        outboxDir
        outboxGroup
        permissionsDir
        skill
        socketPath
        ;
    in
    ''
      import json
      import shlex

      DOMAIN = "${domain}"
      PASSWORD = "${password}"
      # The clients run out of the skill the module ships, so a file missing from it fails here
      MAILPY = "${skill}/mail.py"
      WATCH = "${skill}/mail-watch.py"

      def as_alice(command):
          return f"su alice -c {shlex.quote(command)}"

      def cli(*args):
          return as_alice("python3 " + MAILPY + " " + " ".join(shlex.quote(a) for a in args))

      def deliver(sender, subject, to="agent"):
          machine.succeed(
              f"swaks --server localhost --port 25 --from {sender} --to {to}@{DOMAIN} "
              f"--header {shlex.quote('Subject: ' + subject)} --body hello --timeout 30"
          )

      def inbox_holds(subject, user="agent"):
          machine.wait_until_succeeds(
              f"grep -rl {shlex.quote(subject)} /var/vmail/{DOMAIN}/{user}/Maildir", timeout=30
          )

      machine = mail
      machine.wait_for_unit("dovecot.service")
      machine.wait_for_unit("postfix.service")
      machine.wait_for_unit("sockets.target")
      machine.wait_for_open_port(993)
      machine.wait_for_open_port(465)

      machine.succeed(
          "printf 'allow: list\\nallow friend@${domain}: read, reply, send, delete, media\\n"
          "allow other@${domain}: send\\n"
          "allow to alias-ci@${domain}: read\\n' > ${permissionsDir}/mailboxes/agent.conf"
      )
      machine.succeed("systemctl start mail-agent.service")

      with subtest("only the socket user may talk to the service"):
          assert machine.succeed("stat -c '%U %a' ${socketPath}").strip() == "alice 600"
          machine.succeed(as_alice("test -w ${socketPath}"))
          machine.fail("su bob -c 'test -w ${socketPath}'")

      with subtest("the password reaches no argument, environment or user"):
          pid = machine.succeed("systemctl show -p MainPID --value mail-agent.service").strip()
          machine.fail(f"tr '\\0' '\\n' < /proc/{pid}/cmdline | grep -F {PASSWORD}")
          machine.fail(f"tr '\\0' '\\n' < /proc/{pid}/environ | grep -F {PASSWORD}")
          machine.fail(as_alice("cat /run/credentials/mail-agent.service/${
            if private then "config" else "imap-password-agent"
          }"))

      with subtest("the permission files belong to root, and the agent's user cannot write them"):
          assert machine.succeed("stat -c '%U %a' ${permissionsDir}/mailboxes").strip() == "root 755"
          machine.fail(as_alice("touch ${permissionsDir}/mailboxes/agent.conf"))

      with subtest("a listing shows the granted message and only counts the other"):
          deliver(f"friend@{DOMAIN}", "granted hello")
          deliver(f"stranger@{DOMAIN}", "secret plan")
          inbox_holds("secret plan")
          out = machine.succeed(cli("read", "--mailbox", "agent"))
          assert "granted hello" in out, out
          assert "secret plan" not in out, out
          assert '"withheld": 1' in out, out

      with subtest("reading leaves the message unseen"):
          machine.succeed(cli("read", "--mailbox", "agent", "--search", "granted"))
          flags = machine.succeed(f"doveadm fetch -u agent@{DOMAIN} flags mailbox INBOX all")
          assert "\\Seen" not in flags, flags

      with subtest("a watcher is pushed the granted message and never the other"):
          machine.succeed(as_alice(f"python3 {WATCH} --mailbox agent > /tmp/watch.log 2>&1 &"))
          # Mail that came before the watcher took its mark is old and never shown, so
          # each attempt sends a fresh ping until one is: from then on nothing is missed
          machine.wait_until_succeeds(
              f"swaks --server localhost --port 25 --from friend@{DOMAIN} --to agent@{DOMAIN} "
              f"--header 'Subject: ping' --body x --timeout 30 >/dev/null; sleep 1; "
              f"grep -F ' ping' /tmp/watch.log",
              timeout=60,
          )
          deliver(f"stranger@{DOMAIN}", "pushed secret")
          deliver(f"friend@{DOMAIN}", "pushed hello")
          machine.wait_until_succeeds("grep -F 'pushed hello' /tmp/watch.log", timeout=60)
          watched = machine.succeed("cat /tmp/watch.log")
          assert "pushed secret" not in watched, watched
          assert watched.count("pushed hello") == 1, watched

      with subtest("mail to a granted alias is pushed though its sender is no grant"):
          deliver(f"bot@{DOMAIN}", "alias news", to="alias-ci")
          machine.wait_until_succeeds("grep -F 'alias news' /tmp/watch.log", timeout=60)

      with subtest("the watcher outlives a restart of the service"):
          machine.succeed("systemctl restart mail-agent.service")
          deliver(f"friend@{DOMAIN}", "after restart")
          machine.wait_until_succeeds("grep -F 'after restart' /tmp/watch.log", timeout=90)

      with subtest("a send with one ungranted recipient sends nothing"):
          out = machine.fail(cli("send", "--mailbox", "agent", "--to", f"friend@{DOMAIN}",
                                  "--cc", f"stranger@{DOMAIN}", "--subject", "nope", "--text", "x"))
          assert f"allow stranger@{DOMAIN}:" in out, out

      with subtest("a send reaches its Bcc without naming it, and is filed in Sent"):
          machine.succeed(cli("send", "--mailbox", "agent", "--to", f"friend@{DOMAIN}",
                               "--bcc", f"other@{DOMAIN}", "--subject", "out now", "--text", "x"))
          inbox_holds("out now", user="friend")
          inbox_holds("out now", user="other")
          machine.fail(f"grep -rl 'other@{DOMAIN}' /var/vmail/{DOMAIN}/friend/Maildir")
          machine.wait_until_succeeds(f"grep -rl 'out now' /var/vmail/{DOMAIN}/agent/Maildir/.Sent", timeout=30)
          machine.fail(f"grep -rl 'other@{DOMAIN}' /var/vmail/{DOMAIN}/agent/Maildir/.Sent")

      def uid_of(subject):
          listed = json.loads(machine.succeed(cli("read", "--mailbox", "agent", "--search", subject)))
          return str(listed["result"]["messages"][0]["uid"])

      with subtest("an attachment lands in the outbox, readable by a person"):
          machine.succeed(f"swaks --server localhost --port 25 --from friend@{DOMAIN} --to agent@{DOMAIN} --data @${withAttachment}")
          inbox_holds("the report")
          saved = json.loads(machine.succeed(cli("media", "--mailbox", "agent", "--uid", uid_of("the report"))))
          path = saved["result"]["files"][0]["path"]
          assert path.startswith("${outboxDir}/"), path
          assert "the attached text" in machine.succeed(as_alice(f"cat {shlex.quote(path)}"))
          assert machine.succeed(f"stat -c %G {shlex.quote(path)}").strip() == "${outboxGroup}"

      with subtest("delete moves to Trash"):
          machine.succeed(cli("delete", "--mailbox", "agent", "--uids", uid_of("granted hello")))
          machine.wait_until_succeeds(f"grep -rl 'granted hello' /var/vmail/{DOMAIN}/agent/Maildir/.Trash", timeout=30)
    '';
}
