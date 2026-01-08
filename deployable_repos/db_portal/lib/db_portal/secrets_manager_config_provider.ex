defmodule DbPortal.SecretsManagerConfigProvider do
  @moduledoc """
  Fetches application secrets from AWS Secrets Manager.
  
  The secret (devopshero/{app_name}/secrets) is a JSON object with keys:
  - secret_key_base: Phoenix secret key base
  - signing_salt: Phoenix signing salt for cookies/sessions
  - slack_token: Slack API token (optional, can be "disabled")
  """
  alias __MODULE__
  alias DbPortal.DbMetadata.Aws

  # Secret name in AWS Secrets Manager (created by DevOpsHero CDK)
  # Stored in struct so it's accessible in the protocol implementation
  @default_secret_name "devopshero/db-portal/secrets"

  defstruct [finch_name: nil, secret_name: @default_secret_name]

  defimpl Vapor.Provider do
    def load(%SecretsManagerConfigProvider{secret_name: secret_name} = p) do
      # we want Finch running temporarily to load secrets.  We want to stop it
      # so that we can later start it as part of the normal application supervisor
      # in application.ex
      {:ok, finch_pid} = Finch.start_link(name: p.finch_name)
      try do
        load_secrets(secret_name)
      after
        Process.exit(finch_pid, :normal) # note the pid is the Finch supervisor
      end
    end

    defp load_secrets(secret_name) do
      with {:ok, %{"SecretString" => secret_json}, _} <- Aws.get_secret_value(secret_name),
           {:ok, secrets} <- Jason.decode(secret_json) do
        
        # Return the secrets in the format expected by application.ex
        {:ok, %{
          "signing_salt" => Map.get(secrets, "signing_salt", ""),
          "secret_key_base" => Map.get(secrets, "secret_key_base", ""),
          :slack_token => Map.get(secrets, "slack_token", "disabled")
        }}
      else
        {:error, {:http_error, _, e}} -> 
          {:error, "Failed retrieving secrets from #{secret_name}: #{Map.get(e, "message")}"}
        {:error, e} -> 
          {:error, "Failed retrieving secrets from #{secret_name}: #{inspect(e)}"}
      end
    end
  end
end
