defmodule DbPortal.SecretsManagerConfigProvider do
  @moduledoc """
  the `key` and `value` will be fetched from AWS SecretsManager
  The value will be decrypted using the key, and is expected to be
  a JSON structure that will be returned
  """
  alias __MODULE__
  alias DbPortal.DbMetadata.Aws


  defstruct [key: nil, value: nil, finch_name: nil, slack_token: nil]

  defimpl Vapor.Provider do
    def load(%SecretsManagerConfigProvider{}=p) do
      # we want Finch running temporarily to load secrets.  We want to stop it
      # so that we can later start it as part of the normal application supervisor
      # in application.ex
      {:ok, finch_pid} = Finch.start_link(name: p.finch_name)
      try do
        load_secrets(p)
      after
        Process.exit(finch_pid, :normal) # note the pid is the Finch supervisor
      end
    end

    defp load_secrets(p) do
      # careful about failures so we don't leak secrets
      with {:ok, %{"SecretString"=>decoding_key}, _} <-
             Aws.get_secret_value(p.key),

           {:ok, %{"SecretString"=>vals}, _} <-
             Aws.get_secret_value(p.value),

           {:ok, %{"SecretString"=>slack_token}, _} <-
             Aws.get_secret_value(p.slack_token),

           # do NOT use `decode!` to help ensure no secrets leak if the
           # decode fails
           slack_decoded <- slack_token
                           |> Encrypt.decrypt(decoding_key),

           {:ok, results} <- vals
                           |> Encrypt.decrypt(decoding_key)
                           |> Jason.decode
      do
        {:ok, Map.put(results, :slack_token, slack_decoded)}
      else
        {:error, {:http_error,_,e}} -> {:error, "Failed retrieving secrets needed for config: #{Map.get(e, "message")}"}
        {:error, e} -> {:error, "Failed retrieving secrets needed for config: #{inspect e}"}
      end
    end
  end

end
