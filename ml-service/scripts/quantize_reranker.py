import os
from transformers import AutoTokenizer
from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
from optimum.onnxruntime.configuration import AutoQuantizationConfig

def main():
    model_id = "BAAI/bge-reranker-base"
    save_dir = "./models/bge-reranker-int8"
    os.makedirs(save_dir, exist_ok=True)

    print(f"1. Downloading {model_id}...")
    model = ORTModelForSequenceClassification.from_pretrained(model_id, export=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    model.save_pretrained(save_dir)
    tokenizer.save_pretrained(save_dir)

    print("2. Applying INT8 Quantization...")
    quantizer = ORTQuantizer.from_pretrained(model)
    # AVX2 is optimized for your local Windows CPU
    dqconfig = AutoQuantizationConfig.avx2(is_static=False, per_channel=False)

    quantizer.quantize(save_dir=save_dir, quantization_config=dqconfig)
    
    print(f"✅ Success! Reranker saved to {save_dir}/model_quantized.onnx")

if __name__ == "__main__":
    main()