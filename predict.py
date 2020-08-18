import argparse
import logging

import pandas as pd
import koco
import torch
import torch.nn.functional as F
from omegaconf import OmegaConf
from transformers import BertForSequenceClassification

from utils import get_device_and_ngpus, makedirs

logger = logging.getLogger(__name__)
device, n_gpus = get_device_and_ngpus()
result_dir = "results"
makedirs(result_dir)


def main(conf, dev, save):
    # Load saved data
    checkpoint_path = f"{conf.checkpoint_dir}/{conf.model_name}.pt"
    log_path = f"{conf.log_dir}/{conf.model_name}.log"
    saved_model = torch.load(checkpoint_path, map_location=device)["model"]
    saved_data = torch.load(log_path, map_location=device)
    tokenizer = saved_data['tokenizer']
    config = saved_data["config"]
    label2idx = saved_data["classes"]
    idx2label = {idx: label for label, idx in label2idx.items()}

    if dev:
        test = koco.load_dataset("korean-hate-speech", mode="train_dev")
        test = test["dev"]
    elif config.label.hate and config.label.bias:
        df = pd.read_csv('korean-hate-speech-dataset/labeled/test.bias.ternary.tsv', sep='\t')
        test = []
        for i, row in df.iterrows():
            test.append({'comments': row['comments'], 'bias': row['label']})
    else:
        test = koco.load_dataset("korean-hate-speech", mode="test")

    test_texts = []
    for t in test:
        test_text = t['comments']
        if config.label.hate and config.label.bias:
            bias_context = f'<{t["bias"]}>'
            test_text = f'{bias_context} {test_text}'
        test_texts.append(test_text)

    #  test_texts = [t["comments"] for t in test]
    #  test_texts = ['북극곰', '북극성', '기운을 북돋아주는 글이네요.', '북돋아줄게']
    #  test_texts = ['이병헌', '전현무', '승리', '조민기']

    with torch.no_grad():
        # Declare model and load pre-trained weights
        model = BertForSequenceClassification.from_pretrained(
            config.pretrained_model, num_labels=len(label2idx)
        )
        if config.tokenizer.register_names:
            model.resize_token_embeddings(len(tokenizer))
        elif config.label.hate and config.label.bias:
            model.resize_token_embeddings(len(tokenizer))
        model.load_state_dict(saved_model)
        model.to(device)

        # Predict!
        model.eval()
        y_hats, tokens = [], []
        for index in range(0, len(test_texts), config.train_hparams.batch_size):
            batch = test_texts[index: index + config.train_hparams.batch_size]
            batch_tokenized = tokenizer(
                batch, padding=True, truncation=True, return_tensors="pt"
            )
            x = batch_tokenized["input_ids"]
            mask = batch_tokenized["attention_mask"]
            x = x.to(device)
            mask = mask.to(device)

            y_hat = F.softmax(model(x, attention_mask=mask)[0], dim=-1)
            y_hats += [y_hat]

            batch_token_lists = [tokenizer.tokenize(t) for t in batch]
            tokens += batch_token_lists
        y_hats = torch.cat(y_hats, dim=0)  # (len(test), n_classes)
        probs, indices = y_hats.cpu().topk(1)

    # Print!
    if not save:
        for test_text, index, token in zip(test_texts, indices, tokens):
            print(test_text)
            print(' '.join(token))
            print(idx2label[int(index[0])])
            print("======================================================")

    # Save!
    if save:
        # Save test comment + predicted label
        with open(f"{result_dir}/{conf.model_name}.predict", "w") as f:
            f.write("comments" + "\t" + "prediction" + "\n")
            for test_text, index in zip(test_texts, indices):
                f.write(test_text + "\t" + idx2label[int(index[0])] + "\n")
        # Save tokenized test comment + predicted label
        with open(f"{result_dir}/{conf.model_name}.tokens", "w") as f:
            f.write("tokens" + "\t" + "prediction" + "\n")
            for token, index in zip(tokens, indices):
                f.write(' '.join(token) + "\t" + idx2label[int(index[0])] + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path of the config yaml", required=True)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    config = OmegaConf.load(args.config)
    main(config, args.dev, args.save)
