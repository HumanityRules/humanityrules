# DbPortal

This project may expand into a collection of helpful tools for Data
Engineering.

Initially its role will be to help analyze workloads on the DB with
VividCortex.  VividCortex relies on the performance schema for the text of the
queries, resulting in their being truncated at about 1024 characters.  This
means they often lack the full query text, and importantly also the tagging
info we provide in comments at the end.


## Running the app locally:

1. MySQL - expects a mysql server 5.7 or 8.x listening on port 3306 of
   localhost, with root user and empty password.  (Can change this in
   config/dev.exs).  On M1 Mac I used this command:
   ```
    docker run --name mysql -e MYSQL_ROOT_PASSWORD="" -e MYSQL_ALLOW_EMPTY_PASSWORD=yes -d -p 3306:3306 mysql:8-oracle
   ```
   I used docker tag `8-oracle` because it has an arm version.

2.  Ensure no process is already listening on port 15000 (the default port
    for this app to listen on, also configurable in config/dev.exs).  This
    previously was using port 5000 by default, but MacOS already has Control Center listening on that port
    ```rliebling@Richs-MacBook-Pro db_portal % lsof -i -P | grep 5000
    ControlCe   605 rliebling   16u  IPv4 0x1c28b5ddd13a9b91      0t0  TCP *:5000 (LISTEN)
    ControlCe   605 rliebling   17u  IPv6 0x1c28b5ddd1374129      0t0  TCP *:5000 (LISTEN)
    ```
    see https://developer.apple.com/forums/thread/682332
3. Use (`opsh`)[https://git.coursehero.com/techops/ops-console] to get access to AWS.  If new mac, will need `awscli`, using
   eg `pip3 install awscli`.  For `opsh` something like this should do the
   job: `opsh -c "assume -p default"`
   NOTE:  the profile this app will use is determined by this line in
   runtime.exs:
   ```
   default_aws_profile = System.get_env("AWS_DEFAULT_PROFILE", "default")
   ```
4.  Create the db schema and get the elixir dependencies:
   ```
   mix setup
   ```
   This is an alias defined in mix.exs
5. In dev.exs is a line starting with `config :db_portal, :no_secrets_mgr,`.  This makes the app
   mostly work without having to access AWS Secrets Manager.  But, it means it does not get the password
   for Aurora/Mysql (chdbadmin acct).  So, "Refresh Schemas" will not
   work.  Will need to figure out the best thing to do here.  Meanwhile,
   the secrets listed in this line are only about securing access to the
   web app, so not really secret (as these secrets are only used when you
   run in dev mode locally)
6. Your database will be empty of clusters and schemas, leading to failures to load pages
  such as `/unused`. Go to `/readonly` and then click the gear icon in the top right and choose
  `Refresh Schemas`.  Wait a minute or so and the schemas will be loaded.
7. Debugging tips for LiveView:  when running locally with `iex -S mix
   phx.server` the `.iex.exs` file in this repo will have it `import Periscope`,
   which helps debugging.  See docs here: https://hexdocs.pm/periscope/Periscope.html.
   One super convenient function is `assigns_for DbPortalWeb.ReadOnlySchemaSelectorComponent`

### For PTOSC:
  - must have a working perl environment with `DBD::mysql` module
  installed.  On MacOS i gave up trying to get this installed with the
  system perl and instead installed `perlbrew`, following instructions
  here: https://perlbrew.pl/
  - after that, i installed
    - `cpan DBD::mysql`, and
    - `cpan Term::ReadKey`

### Environment variables used (These should not be needed in local dev):
  - MY_OKTA=<anyvalue>
     in dev mode this will enable Okta authentication flow using the okta setup in Rich's okta developer account.  If not set, and in dev, no okta will be used.  Note however that you will likely still have the auth token in your cookie for another hour because without okta, the code basically just sets up the same result in the session (stored in the cookie) as the okta flow would.
  - SSLCERT_MODE=manual
    set to manual to not try to update LetsEncrypt certs
  - RUN_SAMPLER=Y
    to run the sample collection from prod monolith db.  Normally leave unset for local dev and only enable for production.  Only depends on whether this is set or unset
  - LE_MODE=local
    local (the default), staging, or production   Mode for let's encrypt.  Which LE server does it hit.  For 'local', it hits this app running a simulator of LetsEncrypt issuing self-signed
    certs

### Running in Development:

```
iex -S mix phx.server
```

Or, to use Okta in dev:
```
 MY_OKTA=1 iex -S mix phx.server
```

and load `http://dataengr.local:15000` in your browser (note: http, not https)

### Production notes
1. Runs on dataengr.coursehero.io.  can SSH in as `ubuntu` user.  Rich will
   need to install your SSH key.
2. Code lives in /home/ubuntu/code/db_portal
3. I push code to this repo directly, rather than accessing gitlab
   directly as it is not network accessible.
4. I push as `git push prod main:rich/main` where `prod` is a remote setup
   as
   ```
    [remote "prod"]
        url = ssh://ubuntu@dataengr.coursehero.io/home/ubuntu/code/dbportal
        fetch = +refs/heads/*:refs/remotes/prod/*
   ```
  This pushes to the `rich/main` branch in the git repo in the directory
  `/home/ubuntu/code/db_portal`
5.  On the remote host i can then do `git pull` while on the `main`
    branch, which tracks `rich/main` local branch.
6. Then i run `docker build -t dbp2 .` to build the docker image named
       `dbp2`.
7.  Then i restart the web app within the docker-compose.yml:
       ```
       ubuntu@ip-172-30-114-215:~/code/dbportal$ docker-compose stop web
       Stopping dbportal_web_1 ... done
       ubuntu@ip-172-30-114-215:~/code/dbportal$ docker-compose up -d web
       dbportal_db_1 is up-to-date
       Recreating dbportal_web_1 ...
       Recreating dbportal_web_1 ... done
       ```
8. One can go into iex on the prod app as follows:
```
ubuntu@ip-172-30-114-215:~/code/dbportal$ docker exec -it dbportal_web_1 sh
/app $ bin/db_portal remote
Erlang/OTP 25 [erts-13.0.2] [source] [64-bit] [smp:2:2] [ds:2:2:10] [async-threads:1] [jit]

Interactive Elixir (1.13.4) - press Ctrl+C to exit (type h() ENTER for help)
iex(db_portal@f3bf59470822)2>
```

9.  To run migrations on prod, go into `iex` (as above) and then execute
```
iex(db_portal@f3bf59470822)2> DbPortal.Release.migrate
```
10.  To Debug liveviews:
```
iex(db_portal@f3bf59470822)2> Periscope.all_liveviews
%{0 => DbPortalWeb.ReadOnlyLive}
iex(db_portal@f3bf59470822)3> Periscope.all_sockets
%{
  0 => #Phoenix.LiveView.Socket<
    assigns: %{
      __changed__: %{},
      avatar: {:email, "rliebling@coursehero.com"},
      changeset: #Ecto.Changeset<action: nil, changes: %{}, errors: [],
       data: #DbPortalWeb.ReadOnlyLive<>, valid?: true>,
      cols: [],
      email: "rliebling@coursehero.com",
      env: "prod",
      err: nil,
      flash: %{},
      history_list: [
        {"select * from cfw_url_removals limit 5", 6667},
        {"select * \nfrom cfw_duplicate_questions_requests\nwhere question_id = 43086831",
         6627},
        {"select * from sys.innodb_lock_waits", 6427},
        {"show create table cfw_question_routes", 6059},
        {"select visibility from cfw_question_routes limit 10", 6050},
        {"select count(*) from cfw_document_question_queue  where user_id = 100000843799107",
         5882},
        {"select * from cfw_document_question_queue where document_question_queue_id = 2524591",
         5579},
        {"show create table cfw_marketing_school_seeds", 5578},
        {"show indexes from cfw_tutor_question_base_prices", 5016},
        {"show tables;", 3268}
      ],
      live_action: :index,
      rows: [],
      schema: 47
    },
    endpoint: DbPortalWeb.Endpoint,
    id: "phx-FwfdFSS0qXiQ_QIB",
    parent_pid: nil,
    root_pid: #PID<0.3057.0>,
    router: DbPortalWeb.Router,
    transport_pid: #PID<0.3055.0>,
    view: DbPortalWeb.ReadOnlyLive,
    ...
  >
}
iex(db_portal@f3bf59470822)4> Periscope.all_liveviews
%{0 => DbPortalWeb.ReadOnlyLive}
iex(db_portal@f3bf59470822)5> Periscope.socket 0
#Phoenix.LiveView.Socket<
  assigns: %{
    __changed__: %{},
    avatar: {:email, "rliebling@coursehero.com"},
    changeset: #Ecto.Changeset<action: nil, changes: %{}, errors: [],
     data: #DbPortalWeb.ReadOnlyLive<>, valid?: true>,
    cols: [],
    email: "rliebling@coursehero.com",
    env: "prod",
    err: nil,
    flash: %{},
    history_list: [
      {"select * from cfw_url_removals limit 5", 6667},
      {"select * \nfrom cfw_duplicate_questions_requests\nwhere question_id = 43086831",
       6627},
      {"select * from sys.innodb_lock_waits", 6427},
      {"show create table cfw_question_routes", 6059},
      {"select visibility from cfw_question_routes limit 10", 6050},
      {"select count(*) from cfw_document_question_queue  where user_id = 100000843799107",
       5882},
      {"select * from cfw_document_question_queue where document_question_queue_id = 2524591",
       5579},
      {"show create table cfw_marketing_school_seeds", 5578},
      {"show indexes from cfw_tutor_question_base_prices", 5016},
      {"show tables;", 3268}
    ],
    live_action: :index,
    rows: [],
    schema: 47
  },
  endpoint: DbPortalWeb.Endpoint,
  id: "phx-FwfdFSS0qXiQ_QIB",
  parent_pid: nil,
  root_pid: #PID<0.3057.0>,
  router: DbPortalWeb.Router,
  transport_pid: #PID<0.3055.0>,
  view: DbPortalWeb.ReadOnlyLive,
  ...
>
```

## Standard Phoenix stuff
To start your Phoenix server:

  * Install dependencies with `mix deps.get`
  * Create and migrate your database with `mix ecto.setup`
  * Install Node.js dependencies with `npm install` inside the `assets` directory
  * Start Phoenix endpoint with `mix phx.server`

Now you can visit [`localhost:4000`](http://localhost:4000) from your browser.

Ready to run in production? Please [check our deployment guides](https://hexdocs.pm/phoenix/deployment.html).

## Learn more

  * Official website: https://www.phoenixframework.org/
  * Guides: https://hexdocs.pm/phoenix/overview.html
  * Docs: https://hexdocs.pm/phoenix
  * Forum: https://elixirforum.com/c/phoenix-forum
  * Source: https://github.com/phoenixframework/phoenix
