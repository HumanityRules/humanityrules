FROM hexpm/elixir:1.18.1-erlang-27.2-alpine-3.21.0 AS build

# install build dependencies
RUN apk add --no-cache build-base git python3

# prepare build dir
WORKDIR /app

# install hex + rebar
RUN mix local.hex --force && \
    mix local.rebar --force

# set build ENV
ENV MIX_ENV=prod

# install mix dependencies
COPY mix.exs mix.lock ./
COPY config config
RUN mix do deps.get, deps.compile

COPY priv priv
COPY assets assets
# want the lib dir present before purgecss
COPY lib lib

# uncomment COPY if rel/ exists
COPY rel rel
# compile and build release
# NOTE: assets.deploy will handle esbuild/tailwind.  see mix.exs
RUN mix do assets.deploy, compile, release

#
#
# prepare release image
# alpine version should match from the `build` image at the top of this file
#
FROM alpine:3.21.0 AS app

# compare with https://github.com/hexpm/bob/blob/master/priv/scripts/docker/erlang-alpine.dockerfile
RUN apk add --no-cache openssl ncurses-libs ncurses libstdc++ lksctp-tools

# for percona toolkit and aws-cli
RUN apk add --update perl perl-dbi perl-dbd-mysql perl-io-socket-ssl perl-term-readkey aws-cli

WORKDIR /app

RUN chown nobody:nobody /app

USER nobody:nobody

COPY --from=build --chown=nobody:nobody /app/_build/prod/rel/db_portal ./

ENV HOME=/app

CMD ["bin/db_portal", "start"]
