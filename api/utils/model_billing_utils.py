#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from api.db.joint_services.tenant_model_service import get_model_config_from_provider_instance


def get_billing_model_config(billing_tenant_id: str, model_type, model_name: str):
    try:
        return get_model_config_from_provider_instance(billing_tenant_id, model_type, model_name)
    except Exception as exc:
        model_type_value = getattr(model_type, "value", model_type)
        raise LookupError(f"Caller tenant has no compatible {model_type_value} model configured for {model_name}.") from exc
