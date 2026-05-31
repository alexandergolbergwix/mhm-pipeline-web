"""Custom-code modeling file for the MHM Pipeline's NER models.

Provenance + Contents NER use a 2-layer head (Linear → ReLU → Dropout
→ Linear) which doesn't fit HF's stock ``BertForTokenClassification``
(that's a single Linear). To preserve the trained head weights when
pushing to the Hub, the repos register this module via ``auto_map``
so callers can do::

    AutoModel.from_pretrained(repo_id, trust_remote_code=True)

and get the correct architecture loaded.

Note: HF's Inference Providers serverless tier does NOT auto-deploy
custom-code models. These repos are usable as ARTIFACTS (download,
local inference, dedicated Inference Endpoints) — not as drop-in
serverless inference endpoints. The MHM Pipeline's web app keeps
loading the original .pt files at runtime for these two models.
"""

from __future__ import annotations

from typing import Optional

import torch
from torch import nn
from transformers import (
    BertModel,
    BertPreTrainedModel,
    PretrainedConfig,
)


class MhmNerConfig(PretrainedConfig):
    """Config carrying the bert config + the head sizing."""

    model_type = "mhm_ner"

    def __init__(
        self,
        num_labels: int = 7,
        hidden_size: int = 768,
        head_hidden_size: int = 384,
        dropout: float = 0.1,
        id2label: Optional[dict] = None,
        label2id: Optional[dict] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.num_labels       = num_labels
        self.hidden_size      = hidden_size
        self.head_hidden_size = head_hidden_size
        self.dropout          = dropout
        if id2label is not None:
            self.id2label = {int(k): v for k, v in id2label.items()}
        if label2id is not None:
            self.label2id = label2id


class MhmNerModel(BertPreTrainedModel):
    """DictaBERT encoder + 2-layer NER head matching the desktop pipeline.

    State-dict keys:
      bert.*                   — standard BertModel
      ner_intermediate.weight  — (head_hidden_size, hidden_size)
      ner_intermediate.bias    — (head_hidden_size,)
      ner_output.weight        — (num_labels, head_hidden_size)
      ner_output.bias          — (num_labels,)
    """

    config_class = MhmNerConfig
    base_model_prefix = "bert"

    def __init__(self, config: MhmNerConfig) -> None:
        super().__init__(config)
        self.num_labels = config.num_labels
        self.bert = BertModel(config, add_pooling_layer=False)
        self.ner_intermediate = nn.Linear(
            config.hidden_size, config.head_hidden_size,
        )
        self.ner_dropout = nn.Dropout(config.dropout)
        self.ner_output = nn.Linear(
            config.head_hidden_size, config.num_labels,
        )
        self.post_init()

    def forward(
        self,
        input_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        token_type_ids: Optional[torch.Tensor] = None,
        labels: Optional[torch.Tensor] = None,
        **kwargs,
    ):  # noqa: ANN001 — match BertModel's signature loosely
        from transformers.modeling_outputs import TokenClassifierOutput

        outputs = self.bert(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        hidden = outputs.last_hidden_state           # (B, T, H)
        x = torch.relu(self.ner_intermediate(hidden))
        x = self.ner_dropout(x)
        logits = self.ner_output(x)                   # (B, T, num_labels)

        loss = None
        if labels is not None:
            loss_fct = nn.CrossEntropyLoss()
            loss = loss_fct(
                logits.view(-1, self.num_labels),
                labels.view(-1),
            )
        return TokenClassifierOutput(
            loss=loss, logits=logits,
            hidden_states=outputs.hidden_states,
            attentions=outputs.attentions,
        )


__all__ = ["MhmNerConfig", "MhmNerModel"]
