from __future__ import annotations

import os
from datetime import date

from psycopg import sql

from dwh.config import get_dwh_config
from dwh.db import connect


TRANSFORMS = [
    {
        "name": "application",
        "job_code": "TRANSFORM_CRM_RETAIL_APPLICATION",
        "stg_table": "crm_retail_application",
        "ods_table": "crm_retail_application",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_application (
                source_id,
                application_no,
                requested_credit_info_id,
                chosen_credit_info_id,
                final_credit_info_id,
                branch_id,
                credit_manager_id,
                applicant_id,
                sales_channel_id,
                prev_stage_id,
                stage_id,
                status_id,
                created,
                updated,
                is_pre_approved,
                channel,
                source,
                referal,
                product_group_id,
                main_application_id,
                initiator_id,
                last_modified_by_id,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'application_id',
                (s.source_data->>'requested_credit_info_id')::bigint,
                (s.source_data->>'chosen_credit_info_id')::bigint,
                (s.source_data->>'final_credit_info_id')::bigint,
                (s.source_data->>'branch_id')::bigint,
                (s.source_data->>'credit_manager_id')::bigint,
                (s.source_data->>'applicant_id')::bigint,
                (s.source_data->>'sales_channel_id')::bigint,
                s.source_data->>'prev_stage_id',
                s.source_data->>'stage_id',
                s.source_data->>'status_id',
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                (s.source_data->>'is_pre_approved')::boolean,
                s.source_data->>'channel',
                s.source_data->>'source',
                s.source_data->>'referal',
                s.source_data->>'product_group_id',
                (s.source_data->>'main_application_id')::bigint,
                (s.source_data->>'initiator_id')::bigint,
                (s.source_data->>'last_modified_by_id')::bigint,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_application s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                application_no = EXCLUDED.application_no,
                requested_credit_info_id = EXCLUDED.requested_credit_info_id,
                chosen_credit_info_id = EXCLUDED.chosen_credit_info_id,
                final_credit_info_id = EXCLUDED.final_credit_info_id,
                branch_id = EXCLUDED.branch_id,
                credit_manager_id = EXCLUDED.credit_manager_id,
                applicant_id = EXCLUDED.applicant_id,
                sales_channel_id = EXCLUDED.sales_channel_id,
                prev_stage_id = EXCLUDED.prev_stage_id,
                stage_id = EXCLUDED.stage_id,
                status_id = EXCLUDED.status_id,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                is_pre_approved = EXCLUDED.is_pre_approved,
                channel = EXCLUDED.channel,
                source = EXCLUDED.source,
                referal = EXCLUDED.referal,
                product_group_id = EXCLUDED.product_group_id,
                main_application_id = EXCLUDED.main_application_id,
                initiator_id = EXCLUDED.initiator_id,
                last_modified_by_id = EXCLUDED.last_modified_by_id,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_application.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "applicant",
        "job_code": "TRANSFORM_CRM_RETAIL_APPLICANT",
        "stg_table": "crm_retail_applicant",
        "ods_table": "crm_retail_applicant",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_applicant (
                source_id,
                application_id,
                last_name,
                first_name,
                middle_name,
                birth_date,
                birth_place,
                gender_id,
                citizenship_id,
                marital_status_id,
                mobile_phone,
                email,
                total_income,
                approved_income,
                segment_id,
                risk_grade,
                final_risk_grade,
                abs_client_id,
                is_identified,
                created,
                updated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                (s.source_data->>'application_id')::bigint,
                s.source_data->>'last_name',
                s.source_data->>'first_name',
                s.source_data->>'middle_name',
                (s.source_data->>'birth_date')::timestamp,
                s.source_data->>'birth_place',
                (s.source_data->>'gender_id')::bigint,
                (s.source_data->>'citizenship_id')::bigint,
                (s.source_data->>'marital_status_id')::bigint,
                s.source_data->>'mobile_phone',
                s.source_data->>'email',
                (s.source_data->>'total_income')::numeric,
                (s.source_data->>'approved_income')::numeric,
                s.source_data->>'segment_id',
                s.source_data->>'risk_grade',
                s.source_data->>'final_risk_grade',
                (s.source_data->>'abs_client_id')::bigint,
                (s.source_data->>'is_identified')::boolean,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_applicant s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                application_id = EXCLUDED.application_id,
                last_name = EXCLUDED.last_name,
                first_name = EXCLUDED.first_name,
                middle_name = EXCLUDED.middle_name,
                birth_date = EXCLUDED.birth_date,
                birth_place = EXCLUDED.birth_place,
                gender_id = EXCLUDED.gender_id,
                citizenship_id = EXCLUDED.citizenship_id,
                marital_status_id = EXCLUDED.marital_status_id,
                mobile_phone = EXCLUDED.mobile_phone,
                email = EXCLUDED.email,
                total_income = EXCLUDED.total_income,
                approved_income = EXCLUDED.approved_income,
                segment_id = EXCLUDED.segment_id,
                risk_grade = EXCLUDED.risk_grade,
                final_risk_grade = EXCLUDED.final_risk_grade,
                abs_client_id = EXCLUDED.abs_client_id,
                is_identified = EXCLUDED.is_identified,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_applicant.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "credit_info",
        "job_code": "TRANSFORM_CRM_RETAIL_CREDIT_INFO",
        "stg_table": "crm_retail_credit_info",
        "ods_table": "crm_retail_credit_info",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_credit_info (
                source_id,
                product_category_id,
                credit_amount,
                currency_id,
                credit_term,
                application_id,
                created,
                updated,
                credit_purpose_id,
                monthly_payment,
                rate,
                fresh_money,
                brms_matrix_condition_type_id,
                refinance_liabilities,
                expense_dti,
                income_net,
                alt_rate,
                risk_grade,
                is_monitoring_needed,
                expense_refin,
                debt_external_refin,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                (s.source_data->>'product_category_id')::bigint,
                (s.source_data->>'credit_amount')::numeric,
                (s.source_data->>'currency_id')::bigint,
                (s.source_data->>'credit_term')::integer,
                (s.source_data->>'application_id')::bigint,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                (s.source_data->>'credit_purpose_id')::bigint,
                (s.source_data->>'monthly_payment')::numeric,
                (s.source_data->>'rate')::numeric,
                (s.source_data->>'fresh_money')::numeric,
                s.source_data->>'brms_matrix_condition_type_id',
                s.source_data->>'refinance_liabilities',
                (s.source_data->>'expense_dti')::numeric,
                (s.source_data->>'income_net')::numeric,
                (s.source_data->>'alt_rate')::numeric,
                s.source_data->>'risk_grade',
                (s.source_data->>'is_monitoring_needed')::boolean,
                (s.source_data->>'expense_refin')::numeric,
                (s.source_data->>'debt_external_refin')::numeric,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_credit_info s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                product_category_id = EXCLUDED.product_category_id,
                credit_amount = EXCLUDED.credit_amount,
                currency_id = EXCLUDED.currency_id,
                credit_term = EXCLUDED.credit_term,
                application_id = EXCLUDED.application_id,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                credit_purpose_id = EXCLUDED.credit_purpose_id,
                monthly_payment = EXCLUDED.monthly_payment,
                rate = EXCLUDED.rate,
                fresh_money = EXCLUDED.fresh_money,
                brms_matrix_condition_type_id = EXCLUDED.brms_matrix_condition_type_id,
                refinance_liabilities = EXCLUDED.refinance_liabilities,
                expense_dti = EXCLUDED.expense_dti,
                income_net = EXCLUDED.income_net,
                alt_rate = EXCLUDED.alt_rate,
                risk_grade = EXCLUDED.risk_grade,
                is_monitoring_needed = EXCLUDED.is_monitoring_needed,
                expense_refin = EXCLUDED.expense_refin,
                debt_external_refin = EXCLUDED.debt_external_refin,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_credit_info.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "workflow_history",
        "job_code": "TRANSFORM_CRM_RETAIL_WORKFLOW_HISTORY",
        "stg_table": "crm_retail_workflow_history",
        "ods_table": "crm_retail_workflow_history",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_workflow_history (
                source_id,
                application_id,
                prev_stage_id,
                stage_id,
                status_id,
                created_by,
                credit_manager_id,
                manager_decline_reason_id,
                call_center_id,
                call_center_decline_reason_id,
                under_id,
                under_decision_id,
                under_decline_reason_id,
                limit_owner_id,
                limit_owner_decision_id,
                limit_owner_decision_date,
                change_date,
                dsa_id,
                dsa_utm_id,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                (s.source_data->>'application_id')::bigint,
                s.source_data->>'prev_stage_id',
                s.source_data->>'stage_id',
                s.source_data->>'status_id',
                (s.source_data->>'created_by')::bigint,
                (s.source_data->>'credit_manager_id')::bigint,
                (s.source_data->>'manager_decline_reason_id')::bigint,
                (s.source_data->>'call_center_id')::bigint,
                (s.source_data->>'call_center_decline_reason_id')::bigint,
                (s.source_data->>'under_id')::bigint,
                s.source_data->>'under_decision_id',
                (s.source_data->>'under_decline_reason_id')::bigint,
                (s.source_data->>'limit_owner_id')::bigint,
                s.source_data->>'limit_owner_decision_id',
                (s.source_data->>'limit_owner_decision_date')::timestamp,
                (s.source_data->>'change_date')::timestamp,
                (s.source_data->>'dsa_id')::bigint,
                (s.source_data->>'dsa_utm_id')::bigint,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_workflow_history s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                application_id = EXCLUDED.application_id,
                prev_stage_id = EXCLUDED.prev_stage_id,
                stage_id = EXCLUDED.stage_id,
                status_id = EXCLUDED.status_id,
                created_by = EXCLUDED.created_by,
                credit_manager_id = EXCLUDED.credit_manager_id,
                manager_decline_reason_id = EXCLUDED.manager_decline_reason_id,
                call_center_id = EXCLUDED.call_center_id,
                call_center_decline_reason_id = EXCLUDED.call_center_decline_reason_id,
                under_id = EXCLUDED.under_id,
                under_decision_id = EXCLUDED.under_decision_id,
                under_decline_reason_id = EXCLUDED.under_decline_reason_id,
                limit_owner_id = EXCLUDED.limit_owner_id,
                limit_owner_decision_id = EXCLUDED.limit_owner_decision_id,
                limit_owner_decision_date = EXCLUDED.limit_owner_decision_date,
                change_date = EXCLUDED.change_date,
                dsa_id = EXCLUDED.dsa_id,
                dsa_utm_id = EXCLUDED.dsa_utm_id,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_workflow_history.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "product",
        "job_code": "TRANSFORM_CRM_RETAIL_PRODUCT",
        "stg_table": "crm_retail_product",
        "ods_table": "crm_retail_product",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_product (
                source_id,
                code,
                name_ru,
                name_az,
                name_en,
                active,
                abs_code,
                priority,
                card_type,
                card_product,
                base_product_id,
                created,
                updated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'code',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                s.source_data->>'name_en',
                (s.source_data->>'active')::boolean,
                s.source_data->>'abs_code',
                (s.source_data->>'priority')::numeric,
                s.source_data->>'card_type',
                s.source_data->>'card_product',
                (s.source_data->>'base_product_id')::bigint,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_product s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                code = EXCLUDED.code,
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                name_en = EXCLUDED.name_en,
                active = EXCLUDED.active,
                abs_code = EXCLUDED.abs_code,
                priority = EXCLUDED.priority,
                card_type = EXCLUDED.card_type,
                card_product = EXCLUDED.card_product,
                base_product_id = EXCLUDED.base_product_id,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_product.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "stage",
        "job_code": "TRANSFORM_CRM_RETAIL_STAGE",
        "stg_table": "crm_retail_stage",
        "ods_table": "crm_retail_stage",
        "ods_key": "source_id",
        "stg_key": "s._dwh_source_pk",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_stage (
                source_id,
                name_ru,
                name_az,
                name_en,
                order_num,
                is_user_task,
                is_progbar_visible,
                chatbot_status_id,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                s.source_data->>'id',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                s.source_data->>'name_en',
                (s.source_data->>'order_num')::numeric,
                (s.source_data->>'is_user_task')::boolean,
                (s.source_data->>'is_progbar_visible')::boolean,
                s.source_data->>'chatbot_status_id',
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_stage s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                name_en = EXCLUDED.name_en,
                order_num = EXCLUDED.order_num,
                is_user_task = EXCLUDED.is_user_task,
                is_progbar_visible = EXCLUDED.is_progbar_visible,
                chatbot_status_id = EXCLUDED.chatbot_status_id,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_stage.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "status",
        "job_code": "TRANSFORM_CRM_RETAIL_STATUS",
        "stg_table": "crm_retail_status",
        "ods_table": "crm_retail_status",
        "ods_key": "source_id",
        "stg_key": "s._dwh_source_pk",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_status (
                source_id,
                name_ru,
                name_az,
                name_en,
                chatbot_status_id,
                is_referal_formation,
                is_referal_approve,
                is_referal_decline,
                online_status_id,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                s.source_data->>'id',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                s.source_data->>'name_en',
                s.source_data->>'chatbot_status_id',
                (s.source_data->>'is_referal_formation')::boolean,
                (s.source_data->>'is_referal_approve')::boolean,
                (s.source_data->>'is_referal_decline')::boolean,
                s.source_data->>'online_status_id',
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_status s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                name_en = EXCLUDED.name_en,
                chatbot_status_id = EXCLUDED.chatbot_status_id,
                is_referal_formation = EXCLUDED.is_referal_formation,
                is_referal_approve = EXCLUDED.is_referal_approve,
                is_referal_decline = EXCLUDED.is_referal_decline,
                online_status_id = EXCLUDED.online_status_id,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_status.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "currency",
        "job_code": "TRANSFORM_CRM_RETAIL_DIR_CURRENCY",
        "stg_table": "crm_retail_currency",
        "ods_table": "crm_retail_currency",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_currency (
                source_id,
                code,
                name_ru,
                name_az,
                name_en,
                active,
                created,
                updated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'code',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                s.source_data->>'name_en',
                (s.source_data->>'active')::boolean,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_currency s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                code = EXCLUDED.code,
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                name_en = EXCLUDED.name_en,
                active = EXCLUDED.active,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_currency.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "sales_channel",
        "job_code": "TRANSFORM_CRM_RETAIL_DIR_SALES_CHANNEL",
        "stg_table": "crm_retail_sales_channel",
        "ods_table": "crm_retail_sales_channel",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_sales_channel (
                source_id,
                code,
                name_ru,
                name_az,
                name_en,
                active,
                priority,
                channel_limit,
                created,
                updated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'code',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                s.source_data->>'name_en',
                (s.source_data->>'active')::boolean,
                (s.source_data->>'priority')::numeric,
                (s.source_data->>'limit')::numeric,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_sales_channel s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                code = EXCLUDED.code,
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                name_en = EXCLUDED.name_en,
                active = EXCLUDED.active,
                priority = EXCLUDED.priority,
                channel_limit = EXCLUDED.channel_limit,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_sales_channel.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "branch",
        "job_code": "TRANSFORM_CRM_RETAIL_DIR_BRANCH",
        "stg_table": "crm_retail_branch",
        "ods_table": "crm_retail_branch",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_branch (
                source_id,
                code,
                name_ru,
                name_az,
                active,
                city,
                address,
                voen,
                bic,
                correspondent_account,
                swift,
                created,
                updated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'code',
                s.source_data->>'name_ru',
                s.source_data->>'name_az',
                (s.source_data->>'active')::boolean,
                s.source_data->>'city',
                s.source_data->>'address',
                s.source_data->>'voen',
                s.source_data->>'bic',
                s.source_data->>'correspondent_account',
                s.source_data->>'swift',
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_branch s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                code = EXCLUDED.code,
                name_ru = EXCLUDED.name_ru,
                name_az = EXCLUDED.name_az,
                active = EXCLUDED.active,
                city = EXCLUDED.city,
                address = EXCLUDED.address,
                voen = EXCLUDED.voen,
                bic = EXCLUDED.bic,
                correspondent_account = EXCLUDED.correspondent_account,
                swift = EXCLUDED.swift,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_branch.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "users",
        "job_code": "TRANSFORM_CRM_RETAIL_USERS",
        "stg_table": "crm_retail_user",
        "ods_table": "crm_retail_user",
        "ods_key": "source_id",
        "stg_key": "(s._dwh_source_pk)::bigint",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_user (
                source_id,
                code,
                username,
                fio,
                first_name,
                last_name,
                patronymic,
                email,
                mobile_phone,
                position,
                active,
                dir_branch_id,
                dir_department_id,
                created,
                updated,
                deactivated,
                source_row_hash,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                (s.source_data->>'id')::bigint,
                s.source_data->>'code',
                s.source_data->>'username',
                s.source_data->>'fio',
                s.source_data->>'first_name',
                s.source_data->>'last_name',
                s.source_data->>'patronymic',
                s.source_data->>'email',
                s.source_data->>'mobile_phone',
                s.source_data->>'position',
                (s.source_data->>'active')::boolean,
                (s.source_data->>'dir_branch_id')::bigint,
                (s.source_data->>'dir_department_id')::bigint,
                (s.source_data->>'created')::timestamp,
                (s.source_data->>'updated')::timestamp,
                (s.source_data->>'deactivated')::timestamp,
                s._dwh_row_hash,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_user s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_id)
            DO UPDATE SET
                code = EXCLUDED.code,
                username = EXCLUDED.username,
                fio = EXCLUDED.fio,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                patronymic = EXCLUDED.patronymic,
                email = EXCLUDED.email,
                mobile_phone = EXCLUDED.mobile_phone,
                position = EXCLUDED.position,
                active = EXCLUDED.active,
                dir_branch_id = EXCLUDED.dir_branch_id,
                dir_department_id = EXCLUDED.dir_department_id,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                deactivated = EXCLUDED.deactivated,
                source_row_hash = EXCLUDED.source_row_hash,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
            WHERE ods.crm_retail_user.source_row_hash
                  IS DISTINCT FROM EXCLUDED.source_row_hash
        """,
    },

    {
        "name": "integration_log",
        "job_code": "TRANSFORM_CRM_RETAIL_INTEGRATION_LOG",
        "stg_table": "crm_retail_integration_log",
        "ods_table": "crm_retail_integration_event",
        "ods_key": "source_event_hash",
        "stg_key": "s._dwh_source_pk",
        "upsert_sql": """
            INSERT INTO ods.crm_retail_integration_event (
                source_event_hash,
                service_code,
                application_id,
                applicant_id,
                fin_code,
                guid,
                rq_date,
                rs_date,
                status_name,
                status_code,
                status_message,
                response_ms,
                source_data,
                dwh_source_batch_run_id
            )
            SELECT
                s._dwh_source_pk,
                s.source_data->>'service_code',
                (s.source_data->>'application_id')::bigint,
                (s.source_data->>'applicant_id')::bigint,
                s.source_data->>'fin_code',
                s.source_data->>'guid',
                (s.source_data->>'rq_date')::timestamp,
                (s.source_data->>'rs_date')::timestamp,
                s.source_data->>'status_name',
                s.source_data->>'status_code',
                s.source_data->>'status_message',
                CASE
                    WHEN s.source_data->>'rq_date' IS NOT NULL
                     AND s.source_data->>'rs_date' IS NOT NULL
                    THEN ROUND(
                        EXTRACT(
                            EPOCH FROM (
                                (s.source_data->>'rs_date')::timestamp
                                -
                                (s.source_data->>'rq_date')::timestamp
                            )
                        ) * 1000
                    )::bigint
                    ELSE NULL
                END,
                s.source_data,
                s._dwh_batch_run_id
            FROM stg.crm_retail_integration_log s
            WHERE s._dwh_batch_run_id = %s

            ON CONFLICT (source_event_hash)
            DO UPDATE SET
                service_code = EXCLUDED.service_code,
                application_id = EXCLUDED.application_id,
                applicant_id = EXCLUDED.applicant_id,
                fin_code = EXCLUDED.fin_code,
                guid = EXCLUDED.guid,
                rq_date = EXCLUDED.rq_date,
                rs_date = EXCLUDED.rs_date,
                status_name = EXCLUDED.status_name,
                status_code = EXCLUDED.status_code,
                status_message = EXCLUDED.status_message,
                response_ms = EXCLUDED.response_ms,
                source_data = EXCLUDED.source_data,
                dwh_source_batch_run_id = EXCLUDED.dwh_source_batch_run_id,
                dwh_last_loaded_at = now()
        """,
    },
]


def get_job(conn, code: str) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                job_id,
                source_object_id
            FROM etl.job
            WHERE code = %s
              AND job_type = 'TRANSFORM'
              AND is_active = true
            """,
            (code,),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            f"Transform job not found: {code}"
        )

    return row[0], row[1]


def calculate_stats(
    conn,
    item: dict,
    source_batch_run_id: int,
) -> tuple[int, int, int, int]:

    query = sql.SQL(
        """
        SELECT
            COUNT(*) AS rows_read,

            COUNT(*) FILTER (
                WHERE o.{ods_key} IS NULL
            ) AS rows_inserted,

            COUNT(*) FILTER (
                WHERE o.{ods_key} IS NOT NULL
                  AND o.{hash_expr}
                      IS DISTINCT FROM s._dwh_row_hash
            ) AS rows_updated,

            COUNT(*) FILTER (
                WHERE o.{ods_key} IS NOT NULL
                  AND o.{hash_expr} = s._dwh_row_hash
            ) AS rows_unchanged

        FROM stg.{stg_table} s

        LEFT JOIN ods.{ods_table} o
          ON o.{ods_key} = {stg_key}

        WHERE s._dwh_batch_run_id = %s
        """
    ).format(
        ods_key=sql.Identifier(item["ods_key"]),
        hash_expr=(
            sql.Identifier("source_event_hash")
            if item["name"] == "integration_log"
            else sql.Identifier("source_row_hash")
        ),
        stg_table=sql.Identifier(item["stg_table"]),
        ods_table=sql.Identifier(item["ods_table"]),
        stg_key=sql.SQL(item["stg_key"]),
    )

    with conn.cursor() as cur:
        cur.execute(
            query,
            (source_batch_run_id,),
        )

        row = cur.fetchone()

    return (
        int(row[0]),
        int(row[1]),
        int(row[2]),
        int(row[3]),
    )


PIPELINE_CODE = "ODS_TRANSFORM"
EXPECTED_STG_TO_ODS_MAPPING_COUNT = 207


def normalize_load_mode() -> str:
    mode = os.getenv(
        "LOAD_MODE",
        "FULL",
    ).strip().upper()

    if mode not in {
        "FULL",
        "CONTINUE",
    }:
        raise RuntimeError(
            "LOAD_MODE must be FULL or CONTINUE"
        )

    return mode


def validate_source_batch(
    conn,
    source_batch_run_id: int,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                business_date,
                status
            FROM etl.batch_run
            WHERE batch_run_id = %s
            """,
            (source_batch_run_id,),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            f"Source batch not found: "
            f"{source_batch_run_id}"
        )

    business_date, status = row

    if status != "SUCCESS":
        raise RuntimeError(
            "Source batch must be SUCCESS; "
            f"actual={status}"
        )

    return business_date


def find_latest_execution_group(
    conn,
    source_batch_run_id: int,
):
    """
    Return the latest attempt of the latest ODS root
    for this source batch.

    Older roots are deliberately ignored.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest_per_root AS (
                SELECT DISTINCT ON (
                    root_batch_run_id
                )
                    root_batch_run_id,
                    batch_run_id,
                    attempt_no,
                    status,
                    run_mode,
                    started_at
                FROM etl.batch_run
                WHERE pipeline_code = %s
                  AND source_batch_run_id = %s
                  AND root_batch_run_id IS NOT NULL
                ORDER BY
                    root_batch_run_id,
                    attempt_no DESC,
                    batch_run_id DESC
            )
            SELECT
                root_batch_run_id,
                batch_run_id,
                attempt_no,
                status,
                run_mode
            FROM latest_per_root
            ORDER BY root_batch_run_id DESC
            LIMIT 1
            """,
            (
                PIPELINE_CODE,
                source_batch_run_id,
            ),
        )

        return cur.fetchone()


def load_previous_successes(
    conn,
    *,
    source_batch_run_id: int,
    root_batch_run_id: int,
):
    """
    For one execution root, return the latest
    successful job_run for every completed job.

    Success from another FULL root is never reused.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (jr.job_id)
                jr.job_id,
                jr.job_run_id
            FROM etl.job_run jr

            JOIN etl.batch_run br
              ON br.batch_run_id =
                 jr.batch_run_id

            WHERE br.pipeline_code = %s
              AND br.source_batch_run_id = %s
              AND br.root_batch_run_id = %s
              AND jr.status = 'SUCCESS'

            ORDER BY
                jr.job_id,
                br.attempt_no DESC,
                jr.job_run_id DESC
            """,
            (
                PIPELINE_CODE,
                source_batch_run_id,
                root_batch_run_id,
            ),
        )

        rows = cur.fetchall()

    return {
        int(job_id): int(job_run_id)
        for job_id, job_run_id in rows
    }


def run_preflight(
    source_batch_run_id: int,
) -> int:
    """
    Run technical STG validation and return the exact
    validation batch_run_id used by this ODS attempt.
    """

    os.environ[
        "SOURCE_BATCH_RUN_ID"
    ] = str(source_batch_run_id)

    from validate_stg import (
        main as validate_stg_main,
    )

    print()
    print("=" * 60)
    print("PRE-ODS TECHNICAL VALIDATION")
    print("=" * 60)

    try:
        validation_batch_run_id = (
            validate_stg_main()
        )

    except SystemExit as exc:
        code = exc.code

        if code not in (
            None,
            0,
        ):
            raise RuntimeError(
                "STG technical validation failed; "
                "ODS transformation was not started"
            ) from exc

        raise

    if validation_batch_run_id is None:
        raise RuntimeError(
            "STG validation succeeded but did not "
            "return validation_batch_run_id"
        )

    return int(
        validation_batch_run_id
    )


def get_contract_snapshot_ids(
    conn,
    batch_run_id: int,
) -> set[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT contract_id
            FROM meta.batch_contract_snapshot
            WHERE batch_run_id = %s
            ORDER BY contract_id
            """,
            (batch_run_id,),
        )

        rows = cur.fetchall()

    return {
        int(row[0])
        for row in rows
    }


def assert_continue_config_compatible(
    conn,
    *,
    root_batch_run_id: int,
    current_preflight_batch_run_id: int,
) -> None:
    """
    One ODS root must use one consistent technical
    contract configuration.

    If configuration changed after a FAILED attempt,
    CONTINUE is blocked and a new FULL is required.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT preflight_batch_run_id
            FROM etl.batch_run
            WHERE batch_run_id = %s
            """,
            (root_batch_run_id,),
        )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError(
            "ODS root batch not found: "
            f"{root_batch_run_id}"
        )

    root_preflight_batch_run_id = (
        row[0]
    )

    if root_preflight_batch_run_id is None:
        raise RuntimeError(
            "CONTINUE is not allowed for this "
            "legacy ODS root because it has no "
            "configuration snapshot. Use FULL."
        )

    root_contracts = (
        get_contract_snapshot_ids(
            conn,
            int(
                root_preflight_batch_run_id
            ),
        )
    )

    current_contracts = (
        get_contract_snapshot_ids(
            conn,
            current_preflight_batch_run_id,
        )
    )

    if not root_contracts:
        raise RuntimeError(
            "Root preflight has no contract snapshot. "
            "Use FULL."
        )

    if not current_contracts:
        raise RuntimeError(
            "Current preflight has no contract snapshot."
        )

    if root_contracts != current_contracts:
        added = sorted(
            current_contracts
            - root_contracts
        )

        removed = sorted(
            root_contracts
            - current_contracts
        )

        raise RuntimeError(
            "CONTINUE blocked: technical validation "
            "configuration changed since the FULL "
            "attempt. Start a new FULL. "
            f"added_contract_ids={added}; "
            f"removed_contract_ids={removed}"
        )


def link_preflight_to_ods_attempt(
    conn,
    *,
    batch_run_id: int,
    preflight_batch_run_id: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE etl.batch_run
            SET preflight_batch_run_id = %s
            WHERE batch_run_id = %s
              AND status = 'RUNNING'
            """,
            (
                preflight_batch_run_id,
                batch_run_id,
            ),
        )

    conn.commit()

def get_current_mapping_ids(
    conn,
) -> set[int]:
    """
    Exact active STG_TO_ODS mapping versions that
    exist at this moment.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mapping_id
            FROM meta.v_current_mapping
            WHERE source_system_code = 'CRM_RETAIL'
              AND mapping_stage = 'STG_TO_ODS'
              AND is_active = true
            ORDER BY mapping_id
            """
        )

        rows = cur.fetchall()

    mapping_ids = {
        int(row[0])
        for row in rows
    }

    if len(mapping_ids) != (
        EXPECTED_STG_TO_ODS_MAPPING_COUNT
    ):
        raise RuntimeError(
            "Current mapping catalog is not "
            "compatible with transform_ods.py: "
            f"expected="
            f"{EXPECTED_STG_TO_ODS_MAPPING_COUNT}, "
            f"actual={len(mapping_ids)}"
        )

    return mapping_ids


def get_batch_mapping_ids(
    conn,
    batch_run_id: int,
) -> set[int]:

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT mapping_id
            FROM meta.batch_mapping_snapshot
            WHERE batch_run_id = %s
            ORDER BY mapping_id
            """,
            (batch_run_id,),
        )

        rows = cur.fetchall()

    return {
        int(row[0])
        for row in rows
    }


def assert_continue_mapping_compatible(
    conn,
    *,
    root_batch_run_id: int,
) -> None:
    """
    A CONTINUE attempt may reuse successful jobs only
    when its current mapping configuration is exactly
    the same as the FULL root configuration.
    """

    root_mappings = (
        get_batch_mapping_ids(
            conn,
            root_batch_run_id,
        )
    )

    if not root_mappings:
        raise RuntimeError(
            "CONTINUE is not allowed for this "
            "ODS root because it has no mapping "
            "snapshot. Start a new FULL."
        )

    current_mappings = (
        get_current_mapping_ids(conn)
    )

    if root_mappings != current_mappings:
        added = sorted(
            current_mappings
            - root_mappings
        )

        removed = sorted(
            root_mappings
            - current_mappings
        )

        raise RuntimeError(
            "CONTINUE blocked: STG_TO_ODS "
            "mapping configuration changed "
            "since the FULL attempt. "
            "Start a new FULL. "
            f"added_mapping_ids={added}; "
            f"removed_mapping_ids={removed}"
        )


def snapshot_current_mappings(
    conn,
    *,
    batch_run_id: int,
) -> int:
    """
    Freeze exact mapping versions used by this
    ODS attempt.

    mapping_rule rows are immutable historical
    configuration versions. The batch snapshot stores
    their exact IDs.
    """

    mapping_ids = sorted(
        get_current_mapping_ids(conn)
    )

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO
                    meta.batch_mapping_snapshot (
                        batch_run_id,
                        mapping_id
                    )
                VALUES (
                    %s,
                    %s
                )
                """,
                [
                    (
                        batch_run_id,
                        mapping_id,
                    )
                    for mapping_id
                    in mapping_ids
                ],
            )

        conn.commit()

    except Exception:
        conn.rollback()

        close_failed_batch(
            conn,
            batch_run_id=batch_run_id,
            message=(
                "Unable to freeze "
                "STG_TO_ODS mapping snapshot"
            ),
        )

        raise

    return len(mapping_ids)

def resolve_transform_jobs(
    conn,
):
    result = []

    for item in TRANSFORMS:
        job_id, source_object_id = get_job(
            conn,
            item["job_code"],
        )

        result.append(
            {
                "item": item,
                "job_id": int(job_id),
                "source_object_id":
                    int(source_object_id),
            }
        )

    return result


def create_full_attempt(
    conn,
    *,
    source_batch_run_id: int,
    business_date,
):
    attempt_no = 1

    batch_code = (
        "CRM_RETAIL_ODS_"
        + business_date.strftime("%Y%m%d")
        + "_FULL_A1"
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.batch_run (
                batch_code,
                business_date,
                trigger_type,
                status,
                started_at,
                created_by,
                message,

                pipeline_code,
                source_batch_run_id,
                run_mode,
                root_batch_run_id,
                resume_from_batch_run_id,
                attempt_no
            )
            VALUES (
                %s,
                %s,
                'MANUAL',
                'RUNNING',
                now(),
                'ods_orchestrator',
                %s,

                %s,
                %s,
                'FULL',
                NULL,
                NULL,
                %s
            )
            RETURNING batch_run_id
            """,
            (
                batch_code,
                business_date,
                (
                    "ODS FULL execution; "
                    f"source_batch_run_id="
                    f"{source_batch_run_id}"
                ),
                PIPELINE_CODE,
                source_batch_run_id,
                attempt_no,
            ),
        )

        batch_run_id = int(
            cur.fetchone()[0]
        )

        # Root points to itself.
        # This is initialization of the new record,
        # not modification of historical data.
        cur.execute(
            """
            UPDATE etl.batch_run
            SET root_batch_run_id = batch_run_id
            WHERE batch_run_id = %s
            """,
            (batch_run_id,),
        )

    conn.commit()

    return (
        batch_run_id,
        batch_run_id,
        attempt_no,
        None,
    )


def create_continue_attempt(
    conn,
    *,
    source_batch_run_id: int,
    business_date,
    latest_group,
):
    if latest_group is None:
        raise RuntimeError(
            "CONTINUE is not possible: "
            "no previous ODS execution exists "
            "for this source batch"
        )

    (
        root_batch_run_id,
        previous_batch_run_id,
        previous_attempt_no,
        previous_status,
        previous_mode,
    ) = latest_group

    if previous_status != "FAILED":
        raise RuntimeError(
            "CONTINUE is not possible: "
            "the latest ODS execution group "
            f"is {previous_status}, not FAILED"
        )

    attempt_no = (
        int(previous_attempt_no)
        + 1
    )

    batch_code = (
        "CRM_RETAIL_ODS_"
        + business_date.strftime("%Y%m%d")
        + "_CONTINUE_A"
        + str(attempt_no)
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.batch_run (
                batch_code,
                business_date,
                trigger_type,
                status,
                started_at,
                created_by,
                message,

                pipeline_code,
                source_batch_run_id,
                run_mode,
                root_batch_run_id,
                resume_from_batch_run_id,
                attempt_no
            )
            VALUES (
                %s,
                %s,
                'RETRY',
                'RUNNING',
                now(),
                'ods_orchestrator',
                %s,

                %s,
                %s,
                'CONTINUE',
                %s,
                %s,
                %s
            )
            RETURNING batch_run_id
            """,
            (
                batch_code,
                business_date,
                (
                    "ODS CONTINUE execution; "
                    f"source_batch_run_id="
                    f"{source_batch_run_id}; "
                    f"resume_from_batch_run_id="
                    f"{previous_batch_run_id}"
                ),
                PIPELINE_CODE,
                source_batch_run_id,
                root_batch_run_id,
                previous_batch_run_id,
                attempt_no,
            ),
        )

        batch_run_id = int(
            cur.fetchone()[0]
        )

    conn.commit()

    return (
        batch_run_id,
        int(root_batch_run_id),
        attempt_no,
        int(previous_batch_run_id),
    )


def create_job_runs(
    conn,
    *,
    batch_run_id: int,
    load_mode: str,
    jobs,
    previous_successes,
):
    result = {}

    for job in jobs:
        job_id = job["job_id"]

        if (
            load_mode == "CONTINUE"
            and job_id in previous_successes
        ):
            reused_job_run_id = (
                previous_successes[job_id]
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO etl.job_run (
                        batch_run_id,
                        job_id,
                        status,
                        started_at,
                        finished_at,

                        rows_read,
                        rows_inserted,
                        rows_updated,
                        rows_rejected,
                        error_count,

                        message,
                        reused_from_job_run_id,
                        skip_reason
                    )
                    VALUES (
                        %s,
                        %s,
                        'SKIPPED',
                        now(),
                        now(),

                        0,
                        0,
                        0,
                        0,
                        0,

                        %s,
                        %s,
                        'ALREADY_SUCCESSFUL_IN_ROOT'
                    )
                    RETURNING job_run_id
                    """,
                    (
                        batch_run_id,
                        job_id,
                        (
                            "Skipped by CONTINUE; "
                            "successful result reused "
                            "from job_run_id="
                            f"{reused_job_run_id}"
                        ),
                        reused_job_run_id,
                    ),
                )

                job_run_id = int(
                    cur.fetchone()[0]
                )

            result[job_id] = {
                "job_run_id":
                    job_run_id,
                "status":
                    "SKIPPED",
                "reused_from":
                    reused_job_run_id,
            }

        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO etl.job_run (
                        batch_run_id,
                        job_id,
                        status
                    )
                    VALUES (
                        %s,
                        %s,
                        'PENDING'
                    )
                    RETURNING job_run_id
                    """,
                    (
                        batch_run_id,
                        job_id,
                    ),
                )

                job_run_id = int(
                    cur.fetchone()[0]
                )

            result[job_id] = {
                "job_run_id":
                    job_run_id,
                "status":
                    "PENDING",
                "reused_from":
                    None,
            }

    conn.commit()

    return result


def build_pg_diagnostics(
    exc: Exception,
) -> dict:
    diag = getattr(
        exc,
        "diag",
        None,
    )

    result = {
        "exception_type":
            type(exc).__name__,
        "sql_state":
            getattr(
                exc,
                "sqlstate",
                None,
            ),
    }

    if diag is not None:
        for attr in (
            "severity",
            "message_primary",
            "message_detail",
            "message_hint",
            "schema_name",
            "table_name",
            "column_name",
            "constraint_name",
            "datatype_name",
        ):
            value = getattr(
                diag,
                attr,
                None,
            )

            if value is not None:
                result[attr] = value

    return result


def log_runtime_error(
    conn,
    *,
    batch_run_id: int,
    job_run_id: int,
    job_id: int,
    source_object_id: int,
    item,
    exc: Exception,
):
    import json

    diagnostics = (
        build_pg_diagnostics(exc)
    )

    column_name = (
        diagnostics.get(
            "column_name"
        )
    )

    sql_state = (
        diagnostics.get(
            "sql_state"
        )
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etl.error_log (
                batch_run_id,
                job_run_id,
                job_id,
                source_object_id,

                error_stage,
                error_code,
                error_message,

                source_pk,
                application_id,
                applicant_id,

                column_name,
                bad_value,
                expected_value,

                sql_state,
                exception_type,
                diagnostic_context
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,

                'ODS_TRANSFORM',
                'ODS_TRANSFORM_ERROR',
                %s,

                NULL,
                NULL,
                NULL,

                %s,
                NULL,
                NULL,

                %s,
                %s,
                %s::jsonb
            )
            """,
            (
                batch_run_id,
                job_run_id,
                job_id,
                source_object_id,

                str(exc)[:4000],

                column_name,

                sql_state,
                type(exc).__name__,

                json.dumps(
                    {
                        "job_code":
                            item["job_code"],
                        "stg_table":
                            (
                                "stg."
                                + item["stg_table"]
                            ),
                        "ods_table":
                            (
                                "ods."
                                + item["ods_table"]
                            ),
                        "postgres":
                            diagnostics,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def close_failed_batch(
    conn,
    *,
    batch_run_id: int,
    message: str,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE etl.batch_run
            SET
                status = 'FAILED',
                finished_at = now(),
                message =
                    COALESCE(message, '')
                    || %s
            WHERE batch_run_id = %s
              AND status = 'RUNNING'
            """,
            (
                (
                    "; FAILED: "
                    + message[:3000]
                ),
                batch_run_id,
            ),
        )

    conn.commit()


def main() -> None:
    source_batch_run_id = int(
        os.getenv(
            "SOURCE_BATCH_RUN_ID",
            "0",
        )
    )

    if source_batch_run_id <= 0:
        raise RuntimeError(
            "SOURCE_BATCH_RUN_ID is not set"
        )

    load_mode = normalize_load_mode()

    # ========================================================
    # Read-only planning
    # ========================================================

    with connect(
        get_dwh_config()
    ) as planning_conn:

        business_date = (
            validate_source_batch(
                planning_conn,
                source_batch_run_id,
            )
        )

        latest_group = (
            find_latest_execution_group(
                planning_conn,
                source_batch_run_id,
            )
        )

        if (
            load_mode == "CONTINUE"
            and (
                latest_group is None
                or latest_group[3]
                != "FAILED"
            )
        ):
            latest_status = (
                "NONE"
                if latest_group is None
                else latest_group[3]
            )

            raise RuntimeError(
                "CONTINUE is not available. "
                "Latest execution group status="
                f"{latest_status}"
            )

    # ========================================================
    # Mandatory technical preflight
    #
    # If validation fails, ODS attempt is not created.
    # The validation itself has its own history batch.
    # ========================================================

    preflight_batch_run_id = run_preflight(
        source_batch_run_id
    )

    # ========================================================
    # Create immutable execution attempt
    # ========================================================

    config = get_dwh_config()

    with connect(config) as conn:

        jobs = resolve_transform_jobs(
            conn
        )

        if load_mode == "CONTINUE":
            assert_continue_config_compatible(
                conn,
                root_batch_run_id=int(
                    latest_group[0]
                ),
                current_preflight_batch_run_id=(
                    preflight_batch_run_id
                ),
            )

            assert_continue_mapping_compatible(
                conn,
                root_batch_run_id=int(
                    latest_group[0]
                ),
            )

        if load_mode == "FULL":
            (
                batch_run_id,
                root_batch_run_id,
                attempt_no,
                resume_from_batch_run_id,
            ) = create_full_attempt(
                conn,
                source_batch_run_id=(
                    source_batch_run_id
                ),
                business_date=business_date,
            )

            previous_successes = {}

        else:
            (
                batch_run_id,
                root_batch_run_id,
                attempt_no,
                resume_from_batch_run_id,
            ) = create_continue_attempt(
                conn,
                source_batch_run_id=(
                    source_batch_run_id
                ),
                business_date=business_date,
                latest_group=latest_group,
            )

            previous_successes = (
                load_previous_successes(
                    conn,
                    source_batch_run_id=(
                        source_batch_run_id
                    ),
                    root_batch_run_id=(
                        root_batch_run_id
                    ),
                )
            )

        link_preflight_to_ods_attempt(
            conn,
            batch_run_id=batch_run_id,
            preflight_batch_run_id=(
                preflight_batch_run_id
            ),
        )

        frozen_mapping_count = (
            snapshot_current_mappings(
                conn,
                batch_run_id=batch_run_id,
            )
        )

        job_runs = create_job_runs(
            conn,
            batch_run_id=batch_run_id,
            load_mode=load_mode,
            jobs=jobs,
            previous_successes=(
                previous_successes
            ),
        )

        print()
        print("=" * 60)
        print("ODS EXECUTION STARTED")
        print("=" * 60)
        print(
            f"mode             : "
            f"{load_mode}"
        )
        print(
            f"batch_run_id     : "
            f"{batch_run_id}"
        )
        print(
            f"root_batch_run_id: "
            f"{root_batch_run_id}"
        )
        print(
            f"attempt_no       : "
            f"{attempt_no}"
        )
        print(
            f"source_batch     : "
            f"{source_batch_run_id}"
        )

        if resume_from_batch_run_id:
            print(
                f"resume_from      : "
                f"{resume_from_batch_run_id}"
            )

        print()

        total_read = 0
        total_inserted = 0
        total_updated = 0
        total_unchanged = 0
        total_skipped = 0

        try:
            for job in jobs:
                item = job["item"]
                job_id = job["job_id"]
                source_object_id = (
                    job["source_object_id"]
                )

                run_info = (
                    job_runs[job_id]
                )

                job_run_id = (
                    run_info[
                        "job_run_id"
                    ]
                )

                if (
                    run_info["status"]
                    == "SKIPPED"
                ):
                    total_skipped += 1

                    print(
                        f"SKIPPED "
                        f"{item['name']:<18} "
                        f"reused_job_run="
                        f"{run_info['reused_from']}"
                    )

                    continue

                # --------------------------------------------
                # Job becomes RUNNING.
                # PENDING rows after a later failure remain
                # visible as not-started history.
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE etl.job_run
                        SET
                            status = 'RUNNING',
                            started_at = now(),
                            message = %s
                        WHERE job_run_id = %s
                          AND status = 'PENDING'
                        """,
                        (
                            (
                                f"{load_mode}; "
                                f"source_batch_run_id="
                                f"{source_batch_run_id}"
                            ),
                            job_run_id,
                        ),
                    )

                conn.commit()

                (
                    rows_read,
                    rows_inserted,
                    rows_updated,
                    rows_unchanged,
                ) = calculate_stats(
                    conn,
                    item,
                    source_batch_run_id,
                )

                if (
                    rows_inserted
                    + rows_updated
                    + rows_unchanged
                    != rows_read
                ):
                    raise RuntimeError(
                        f"{item['name']}: "
                        "pre-load reconciliation "
                        "failed"
                    )

                print(
                    f"RUNNING "
                    f"{item['name']:<18} "
                    f"read={rows_read:<5} "
                    f"insert={rows_inserted:<5} "
                    f"update={rows_updated:<5} "
                    f"unchanged={rows_unchanged:<5}",
                    flush=True,
                )

                try:

                    # One job = one transaction.
                    # If this SQL fails, only this
                    # object is rolled back.
                    with conn.cursor() as cur:
                        cur.execute(
                            item["upsert_sql"],
                            (
                                source_batch_run_id,
                            ),
                        )

                        cur.execute(
                            """
                            UPDATE etl.job_run
                            SET
                                status = 'SUCCESS',
                                finished_at = now(),

                                rows_read = %s,
                                rows_inserted = %s,
                                rows_updated = %s,
                                rows_rejected = 0,
                                error_count = 0,

                                message = %s
                            WHERE job_run_id = %s
                            """,
                            (
                                rows_read,
                                rows_inserted,
                                rows_updated,
                                (
                                    f"{load_mode}; "
                                    f"unchanged="
                                    f"{rows_unchanged}; "
                                    f"source_batch_run_id="
                                    f"{source_batch_run_id}"
                                ),
                                job_run_id,
                            ),
                        )

                    conn.commit()

                except Exception as exc:
                    # Roll back ONLY the failing
                    # object transaction.
                    conn.rollback()

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE etl.job_run
                            SET
                                status = 'FAILED',
                                finished_at = now(),
                                rows_read = %s,
                                error_count = 1,
                                message = %s
                            WHERE job_run_id = %s
                            """,
                            (
                                rows_read,
                                (
                                    f"{type(exc).__name__}: "
                                    f"{str(exc)[:3500]}"
                                ),
                                job_run_id,
                            ),
                        )

                    log_runtime_error(
                        conn,
                        batch_run_id=(
                            batch_run_id
                        ),
                        job_run_id=job_run_id,
                        job_id=job_id,
                        source_object_id=(
                            source_object_id
                        ),
                        item=item,
                        exc=exc,
                    )

                    conn.commit()

                    close_failed_batch(
                        conn,
                        batch_run_id=(
                            batch_run_id
                        ),
                        message=(
                            f"job="
                            f"{item['job_code']}; "
                            f"{type(exc).__name__}: "
                            f"{str(exc)}"
                        ),
                    )

                    print()
                    print(
                        "ODS EXECUTION FAILED"
                    )
                    print(
                        f"failed job : "
                        f"{item['job_code']}"
                    )
                    print(
                        "Remaining PENDING jobs "
                        "were not executed."
                    )

                    raise

                total_read += rows_read
                total_inserted += (
                    rows_inserted
                )
                total_updated += (
                    rows_updated
                )
                total_unchanged += (
                    rows_unchanged
                )

                print(
                    f"SUCCESS "
                    f"{item['name']}"
                )

            # =================================================
            # All executable jobs completed
            # =================================================

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM etl.job_run
                    WHERE batch_run_id = %s
                      AND status IN (
                          'PENDING',
                          'RUNNING',
                          'FAILED'
                      )
                    """,
                    (batch_run_id,),
                )

                incomplete = int(
                    cur.fetchone()[0]
                )

            if incomplete != 0:
                raise RuntimeError(
                    "ODS execution has "
                    f"{incomplete} incomplete jobs"
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE etl.batch_run
                    SET
                        status = 'SUCCESS',
                        finished_at = now(),
                        message =
                            COALESCE(message, '')
                            || %s
                    WHERE batch_run_id = %s
                    """,
                    (
                        (
                            "; SUCCESS"
                            f"; read={total_read}"
                            f"; inserted="
                            f"{total_inserted}"
                            f"; updated="
                            f"{total_updated}"
                            f"; unchanged="
                            f"{total_unchanged}"
                            f"; skipped_jobs="
                            f"{total_skipped}"
                        ),
                        batch_run_id,
                    ),
                )

            conn.commit()

            print()
            print(
                "ODS EXECUTION SUCCESS"
            )
            print(
                f"batch_run_id : "
                f"{batch_run_id}"
            )
            print(
                f"root         : "
                f"{root_batch_run_id}"
            )
            print(
                f"attempt      : "
                f"{attempt_no}"
            )
            print(
                f"mode         : "
                f"{load_mode}"
            )
            print(
                f"read         : "
                f"{total_read}"
            )
            print(
                f"inserted     : "
                f"{total_inserted}"
            )
            print(
                f"updated      : "
                f"{total_updated}"
            )
            print(
                f"unchanged    : "
                f"{total_unchanged}"
            )
            print(
                f"skipped jobs : "
                f"{total_skipped}"
            )

        except Exception as exc:
            conn.rollback()

            # If the error happened outside the
            # per-job error handler, close this new
            # attempt only. Historical attempts are
            # never modified.
            close_failed_batch(
                conn,
                batch_run_id=batch_run_id,
                message=(
                    f"{type(exc).__name__}: "
                    f"{str(exc)}"
                ),
            )

            raise


if __name__ == "__main__":
    main()