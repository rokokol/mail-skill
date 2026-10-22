{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

# The unit is where the isolation lives, so the module ships with the service rather than
# being written again by each consumer. DynamicUser gives the service an identity no other
# process has, and the passwords arrive as credentials in a namespace nobody else shares;
# the socket is the only way in
let
  cfg = config.services.mail-agent;
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;

  # The same shape the service checks at start, so a name the permission files cannot
  # carry fails the build rather than the unit
  namePattern = "[a-z0-9][a-z0-9-]{0,62}";

  mailbox = types.submodule (
    { config, ... }:
    {
      options = {
        address = mkOption {
          type = types.str;
          description = "The mailbox's own address. New mail goes out from it";
        };

        user = mkOption {
          type = types.str;
          default = config.address;
          defaultText = "the address";
          description = "The login for both IMAP and SMTP";
        };

        passwordFile = mkOption {
          type = types.path;
          description = ''
            A file holding the password for both IMAP and SMTP. systemd reads it before
            the service starts, so a file owned by root and readable by nobody else is
            what this wants
          '';
        };

        imap = {
          host = mkOption {
            type = types.str;
            description = "The IMAP server, reached over TLS from the first byte";
          };
          port = mkOption {
            type = types.port;
            default = 993;
            description = "The IMAP port";
          };
        };

        smtp = {
          host = mkOption {
            type = types.str;
            default = config.imap.host;
            defaultText = "imap.host";
            description = "The submission server, reached over TLS from the first byte";
          };
          port = mkOption {
            type = types.port;
            default = 465;
            description = "The submission port";
          };
        };

        folders = lib.genAttrs [ "sent" "drafts" "trash" ] (
          kind:
          mkOption {
            type = types.nullOr types.str;
            default = null;
            description = ''
              The ${kind} folder, for a server that does not flag it. Left null, the
              folder is the one carrying the RFC 6154 flag
            '';
          }
        );

        appendSent = mkOption {
          type = types.bool;
          default = true;
          description = ''
            Whether the service files a copy of what it sends in the sent folder. Turn it
            off where the server files the copy itself, as Gmail does, or every message
            is there twice
          '';
        };

        ownAddresses = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [
            "github@example.org"
            "/hh-.*@example\\.org/"
          ];
          description = ''
            More addresses that are this mailbox's own, each a literal or a /regex/: its
            aliases. An own address is never a second party in a permission decision, and
            a reply comes from the own address the message reached. The mailbox's address
            is always its own
          '';
        };
      };
    }
  );

  # What the service reads at start. Nothing in it is secret
  declared = pkgs.writeText "mail-agent-mailboxes.json" (
    builtins.toJSON (
      lib.mapAttrs (_: box: {
        inherit (box) address user folders;
        imap_host = box.imap.host;
        imap_port = box.imap.port;
        smtp_host = box.smtp.host;
        smtp_port = box.smtp.port;
        append_sent = box.appendSent;
        own = box.ownAddresses;
      }) cfg.mailboxes
    )
  );
in
{
  options.services.mail-agent = {
    enable = mkEnableOption "the mail agent service";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = "the flake's own mail-agentd";
      description = "The service to run";
    };

    mailboxes = mkOption {
      type = types.attrsOf mailbox;
      default = { };
      description = ''
        The mailboxes, by the name the agent and the permission files use. The name is
        lower-case letters, digits and hyphens. Everything here but the passwords lands in
        the Nix store; configFile keeps the whole of it private instead
      '';
    };

    configFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/run/secrets/mail-agent";
      description = ''
        A JSON file declaring the mailboxes in place of the mailboxes option, passwords
        included, for a configuration whose addresses are not to be published: a sops
        secret, say. systemd hands it to the service as a credential, so it reaches
        neither the Nix store nor an argument list. Its format is in the repository's
        references/setup.md
      '';
    };

    socketUser = mkOption {
      type = types.str;
      description = ''
        Who may talk to the socket. systemd owns its permissions, because a service under
        a dynamic identifier cannot usefully set them for a named user
      '';
    };

    socketPath = mkOption {
      type = types.str;
      default = "/run/mail-agent/socket";
      description = "Where the socket is created";
    };

    permissionsDir = mkOption {
      type = types.path;
      default = "/var/lib/mail-agent-permissions";
      description = ''
        Where the per-mailbox permission files live. The service only reads them. Keep
        them out of reach of whatever writes on the agent's behalf: a permission the
        agent can edit is not a permission
      '';
    };

    outboxDir = mkOption {
      type = types.path;
      default = "/var/lib/mail-agent-outbox";
      description = "Where downloaded attachments land";
    };

    outboxGroup = mkOption {
      type = types.str;
      default = "users";
      description = ''
        The group that may read the outbox. The service joins it, and the directory is
        setgid so a downloaded file keeps it
      '';
    };

    keepMediaDays = mkOption {
      type = types.ints.positive;
      default = 7;
      description = ''
        How long a downloaded file stays before the daily sweep removes it. Without a
        limit the outbox becomes a permanent copy of every attachment ever read
      '';
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = (cfg.mailboxes != { }) != (cfg.configFile != null);
        message = "services.mail-agent takes its mailboxes from either mailboxes or configFile, and from exactly one";
      }
    ]
    ++ map (name: {
      assertion = builtins.match namePattern name != null;
      message = "services.mail-agent.mailboxes.${name}: a name is lower-case letters, digits and hyphens";
    }) (lib.attrNames cfg.mailboxes);

    systemd = {
      # systemd creates both directories, because the service runs as a user that exists
      # only while it runs, and a directory it created would carry a meaningless owner
      tmpfiles.rules = [
        "d ${cfg.permissionsDir} 0755 root root -"
        "d ${cfg.permissionsDir}/mailboxes 0755 root root -"
        "d ${cfg.outboxDir} 2775 root ${cfg.outboxGroup} -"
      ];

      sockets."mail-agent" = {
        description = "Socket for the mail agent service";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = cfg.socketPath;
          SocketUser = cfg.socketUser;
          SocketMode = "0600";
        };
      };

      services = {
        "mail-agent" = {
          description = "Mail agent service";
          requires = [ "mail-agent.socket" ];
          after = [
            "network-online.target"
            "mail-agent.socket"
          ];
          wants = [ "network-online.target" ];

          serviceConfig = {
            ExecStart = lib.concatStringsSep " " [
              (lib.getExe cfg.package)
              # %d is the unit's credentials directory, which systemd fills in
              "--mailboxes ${if cfg.configFile != null then "%d/config" else declared}"
              "--permissions ${cfg.permissionsDir}"
              "--media-dir ${cfg.outboxDir}"
            ];

            # The whole reason this is a unit rather than a script. One password serves both
            # protocols, and the service reads it under two names; a private config file
            # carries its passwords inside and is the one credential
            DynamicUser = true;
            LoadCredential =
              if cfg.configFile != null then
                [ "config:${cfg.configFile}" ]
              else
                lib.concatLists (
                  lib.mapAttrsToList (name: box: [
                    "imap-password-${name}:${box.passwordFile}"
                    "smtp-password-${name}:${box.passwordFile}"
                  ]) cfg.mailboxes
                );

            # The outbox is the one path outside the namespace the service may write
            ReadWritePaths = [ cfg.outboxDir ];
            SupplementaryGroups = [ cfg.outboxGroup ];
            UMask = "0022";

            PrivateMounts = true;
            PrivateDevices = true;
            ProtectHome = true;
            ProtectSystem = "strict";
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            NoNewPrivileges = true;
            Restart = "on-failure";
            RestartSec = "10s";
            # 78 is the service saying a mailbox cannot be configured: a password missing, a
            # rule that does not parse. A restart cannot fix either
            RestartPreventExitStatus = "78";
          };
        };

        "mail-agent-sweep" = {
          description = "Remove downloads that have outlived their keep time";
          serviceConfig = {
            Type = "oneshot";
            ExecStart = "${pkgs.findutils}/bin/find ${cfg.outboxDir} -type f -mtime +${toString cfg.keepMediaDays} -delete";
          };
        };
      };

      # A timer rather than a thread inside the service, so an outbox whose files have
      # outlived their time is emptied even while nothing is being downloaded
      timers."mail-agent-sweep" = {
        description = "Remove downloads that have outlived their keep time";
        wantedBy = [ "timers.target" ];
        timerConfig = {
          OnCalendar = "daily";
          Persistent = true;
        };
      };
    };
  };
}
