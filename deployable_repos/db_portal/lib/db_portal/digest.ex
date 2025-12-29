defmodule DbPortal.Digest do

  use Ecto.Schema
  import Ecto.Changeset
#  alias __MODULE__

  @primary_key false
  schema "digests" do
    field :digest, :string, primary_key: true
    field :mysql_digest, :string
    field :digest_text, :string
    field :query_example, :string
    field :first_seen_at, :utc_datetime
  end

  def from_sample(sample) do
    attrs = Map.from_struct(sample)
            |> Map.put(:first_seen_at, sample.sampled_at)

    cast(%__MODULE__{}, attrs, __schema__(:fields))
    |> Ecto.Changeset.apply_changes
    |> Map.from_struct
    |> Map.delete(:__meta__)
  end

    @doc ~S"""
    Implement hashing of query digests consistent with VividCortex

    ## Example usage

    iex> DbPortal.Digest.hash "commit"
    "fffca4d67ea0a788"

    iex> DbPortal.Digest.hash "COMMIT"
    "fffca4d67ea0a788"

    iex> DbPortal.Digest.hash "select `c?_`.`question_id` as `question_id_?` , `c?_`.`user_id` as `user_id_?` , `c?_`.`display_name` as `display_name_?` , `c?_`.`question` as `question_?` , `c?_`.`deadline` as `deadline_?` , `c?_`.`content_lock_id` as `content_lock_id_?` , `c?_`.`date_asked` as `date_asked_?` , `c?_`.`subject` as `subject_?` , `c?_`.`txt` as `txt_?` , count (distinctrow `c?_`.`qa_thread_id`) as `sclr_?` , `c?_`.`price` as `price_?` , count (distinctrow `c?_`.`expert_question_skip_id`) as `sclr_?` , count (distinctrow `c?_`.`expert_question_skip_id`) as `sclr_?` , max (`c?_`.`created_on`) as `sclr_?` , (case when `c?_`.`sphinx_question_id` is null then ? else ? end) as `sclr_?` , (case when `c?_`.`document_question_id` is null then ? else ? end) as `sclr_?` , `?_`.`tutor_bonus` as `tutor_bonus_?` , `?_`.`num_sub_questions` as `num_sub_questions_?` , `?_`.`question_attachment_id` as `question_attachment_id_?` , `?_`.`users_filename` as `users_filename_?` from `cfw_questions` `c?_` left join"
    "b10e35dc4ea44b15"

    iex> # Note the space at the end of the query
    iex> q="SELECT `t0` . `crop_text_annotation_id` AS `crop_text_annotation_id_1` , `t0` . `annotation_fulfillment_id` AS `annotation_fulfillment_id_2` , `t0` . `annotation_type_id` AS `annotation_type_id_3` , `t0` . `page` AS `page_4` , `t0` . `group_identifier` AS `group_identifier_5` , `t0` . `start_x` AS `start_x_6` , `t0` . `start_y` AS `start_y_7` , `t0` . `width` AS `width_8` , `t0` . `height` AS `height_9` , `t0` . `html_ids` AS `html_ids_10` , `t0` . `text` AS `text_11` , `t0` . `valid` AS `valid_12` , `t0` . `created_on` AS `created_on_13` , `t0` . `last_modified_on` AS `last_modified_on_14` FROM `cfw_crop_text_annotations` `t0` WHERE `t0` . `annotation_fulfillment_id` = ? AND `t0` . `annotation_type_id` = ? AND `t0` . `group_identifier` = ? "
    iex> DbPortal.Digest.hash(q)
    "a9fa456f4029fea3"

    iex> q = "select `files_id` , `filehash` , `type` , `scribd_status` , `fp_status` , `scribd_mod_date` , `fp_mod_date` , `scribd_upload_attempts` , `thumbnail_url` , `amazon_storage` , `tmp_size` , `adv_status` , `adv_mod_date` , `adv_upload_attempts` , `upload_source` from `cfw_files` as `cfw_files` where (`files_id`>=?) and (`files_id`<=?)"
    iex> DbPortal.Digest.hash(q)
    "a772b3b184839035"

    iex> # on seeing a digit, VC treats it as a number to be replaced by a?
    iex> # but, it will consider hex digits too.  if expanding to the right
    iex> # it gets at least 2 digits (eg "12" or "1F") it will also expand
    iex> # to the left with hex digits
    iex> # In the example below, all digits and capital letters are considered
    iex> # part of a numeric literal and replaced with ?.  lower vs uppercase
    iex> # is merely my shorthand for showing what will be replaced before md5
    iex> DbPortal.Digest.hash  "foo aaaaa1 AAAAA12 gggggA1B"
    "4bfcd66f4ed644a0"
    """
  def hash(digest) do

    digest_expanded_collapse = Regex.replace(~r|[a-fA-F]*\d[0-9a-fA-F]+|, digest, "?")
    lower_vc_digest = Regex.replace(~r|\d[0-9a-fA-F]*|, digest_expanded_collapse, "?")
                      |> String.replace(" . ", ".")
                      |> String.replace(" = ", "=")
                      |> String.replace(" >= ", ">=")
                      |> String.replace(" <= ", "<=")
                      |> String.replace("( ", "(")
                      |> String.replace(" )", ")")
                      |> String.downcase
                      |> String.trim

    :crypto.hash(:md5, lower_vc_digest)
    |> Base.encode16(case: :lower) 
    |> String.split_at(16)
    |> elem(0)
  end

end


#   alias DbPortal.Repo
#   alias DbPortal.Digest
# %MyXQL.Result{rows: rows} = Ecto.Adapters.SQL.query!(
#       Repo, ~s"""
#       SELECT digest_text
#       FROM digests
#       WHERE digest='fe13cd7104fc0a52'
#       """
# )
# q = rows|> hd |> hd
# qq = Regex.replace(~r|\d+|, q, "?") |> String.replace(" . ", ".") |> String.replace(" = ", "=") |> String.downcase |> String.trim
# Digest.hash( qq )
# #VC
# v="select `t?`.`crop_text_annotation_id` as `crop_text_annotation_id_?` , `t?`.`annotation_fulfillment_id` as `annotation_fulfillment_id_?` , `t?`.`annotation_type_id` as `annotation_type_id_?` , `t?`.`page` as `page_?` , `t?`.`group_identifier` as `group_identifier_?` , `t?`.`start_x` as `start_x_?` , `t?`.`start_y` as `start_y_?` , `t?`.`width` as `width_?` , `t?`.`height` as `height_?` , `t?`.`html_ids` as `html_ids_?` , `t?`.`text` as `text_?` , `t?`.`valid` as `valid_?` , `t?`.`created_on` as `created_on_?` , `t?`.`last_modified_on` as `last_modified_on_?` from `cfw_crop_text_annotations` `t?` where `t?`.`annotation_fulfillment_id`=? and `t?`.`annotation_type_id`=? and `t?`.`group_identifier`=?"
#
#
#
#
#
# d= "SELECT `c0_` . `question_id` AS `question_id_0` , `c0_` . `user_id` AS `user_id_1` , `c1_` . `display_name` AS `display_name_2` , `c0_` . `question` AS `question_3` , `c0_` . `deadline` AS `deadline_4` , `c2_` . `content_lock_id` AS `content_lock_id_5` , `c0_` . `date_asked` AS `date_asked_6` , `c3_` . `subject` AS `subject_7` , `c4_` . `txt` AS `txt_8` , COUNT ( DISTINCTROW `c5_` . `qa_thread_id` ) AS `sclr_9` , `c0_` . `price` AS `price_10` , COUNT ( DISTINCTROW `c6_` . `user_id` ) AS `sclr_11` , COUNT ( DISTINCTROW `c7_` . `expert_question_skip_id` ) AS `sclr_12` , MAX ( `c7_` . `created_on` ) AS `sclr_13` , ( CASE WHEN `c8_` . `sphinx_question_id` IS NULL THEN ? ELSE ? END ) AS `sclr_14` , ( CASE WHEN `c9_` . `document_question_id` IS NULL THEN ? ELSE ? END ) AS `sclr_15` , `c10_` . `tutor_bonus` AS `tutor_bonus_16` , `c10_` . `num_sub_questions` AS `num_sub_questions_17` , `c11_` . `question_attachment_id` AS `question_attachment_id_18` , `c11_` . `users_filename` AS `users_filename_19` FROM `cfw_questions` `c0_` LEFT JOIN "
#
# q = "SELECT c0_.question_id AS question_id_0, c0_.user_id AS user_id_1, c1_.display_name AS display_name_2, c0_.question AS question_3, c0_.deadline AS deadline_4, c2_.content_lock_id AS content_lock_id_5, c0_.date_asked AS date_asked_6, c3_.subject AS subject_7, c4_.txt AS txt_8, COUNT(DISTINCT c5_.qa_thread_id) AS sclr_9, c0_.price AS price_10, COUNT(DISTINCT c6_.user_id) AS sclr_11, COUNT(DISTINCT c7_.expert_question_skip_id) AS sclr_12, MAX(c7_.created_on) AS sclr_13, (CASE WHEN c8_.sphinx_question_id IS NULL THEN 0 ELSE 1 END) AS sclr_14, (CASE WHEN c9_.document_question_id IS NULL THEN 0 ELSE 1 END) AS sclr_15, c10_.tutor_bonus AS tutor_bonus_16, c10_.num_sub_questions AS num_sub_questions_17, c11_.question_attachment_id AS question_attachment_id_18, c11_.users_filename AS users_filename_19 FROM cfw_questions c0_ LEFT JOIN cfw_questions_additional_flags c10_ ON c0_.question_id = c10_.question_id INNER JOIN cfw_categories c3_ ON (c0_.qa_subject_id = c3_.category_id) AND c3_.taxonomy_type IN ('subject') INNER JOIN cfw_qa_threads c4_ ON c0_.question_id = c4_.question_id INNER JOIN cfw_workflow_jobs c12_ ON (c12_.object_id = c0_.question_id AND c12_.state_id = 4) AND c12_.workflow_id IN ('1') LEFT JOIN cfw_qa_threads c13_ ON c0_.question_id = c13_.question_id AND (c13_.user_id = 100000801769388 AND c13_.type = 'answer') LEFT JOIN cfw_question_answers c5_ ON c0_.question_id = c5_.question_id LEFT JOIN cfw_user_identity c1_ ON (c0_.user_id = c1_.user_id) LEFT JOIN cfw_question_attachments c11_ ON c4_.qa_thread_id = c11_.qa_thread_id LEFT JOIN cfw_content_locks c2_ ON (c2_.content_id = c0_.question_id AND c2_.user_id <> 100000801769388 AND c2_.content_type = 'question' AND c2_.reason = 'Answer in progress' AND (c2_.expires_on > CURRENT_TIMESTAMP AND c2_.released_on IS NULL)) LEFT JOIN cfw_expert_question_skips c6_ ON (c6_.question_id = c0_.question_id) LEFT JOIN cfw_expert_question_skips c7_ ON (c7_.question_id = c0_.question_id AND c7_.user_id = 100000801769388) LEFT JOIN cfw_expert_question_skips_reset_date c14_ ON (c14_.question_id = c0_.question_id) LEFT JOIN cfw_document_questions c9_ ON (c0_.question_id = c9_.question_id) LEFT JOIN cfw_sphinx_questions c8_ ON (c0_.question_id = c8_.question_id) LEFT JOIN cfw_expert_question_skips c15_ ON (c15_.question_id = c0_.question_id AND c15_.user_id = 100000801769388) WHERE c0_.difficulty = 'basic' AND c0_.deadline > CURRENT_TIMESTAMP AND c0_.date_closed > CURRENT_TIMESTAMP AND c0_.deposited = 1 AND c0_.price > 0 AND c4_.first = 1 AND c13_.qa_thread_id IS NULL AND ((c7_.expert_question_skip_id IS NULL OR c7_.created_on > (CASE WHEN c14_.expert_question_skips_reset_date_id IS NOT NULL THEN c14_.reset_date ELSE '0000-00-00 00:00:00' END))) AND c8_.sphinx_question_id IS NULL AND c2_.content_lock_id IS NULL AND c15_.expert_question_skip_id IS NULL AND c0_.qa_subject_id IN (15, 105, 229, 743, 124575053) GROUP BY c0_.question_id, c11_.question_attachment_id ORDER BY sclr_12 ASC, sclr_11 ASC, c0_.date_asked ASC LIMIT 100 OFFSET 0"
# DbPortal.Digest.hash d,q
# "f6c5898c7445dd86"

