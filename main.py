# import sys; sys.argv.extend(["--model-type", "gemini", "--mode", "no_function", "--dataset", "datasets/test_no_tool.json", "--semantic-judge-model", "gemini-1.5-pro-002"])

import os
import json
import asyncio
import argparse
import json
import os
from models import OpenAIModel, GeminiModel
from evaluator import Evaluator
from model_tester import ModelTester
from datetime import datetime
import logging

from utils import process_raw_responses

# Suppress urllib3 connection pool warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.MaxRetryError)

# Configure root logger first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Then configure specific loggers
logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
logging.getLogger('google.auth.transport.requests').setLevel(logging.ERROR)
logging.getLogger('google.oauth2').setLevel(logging.ERROR)

# Suppress Vertex AI engine message
logging.getLogger('google.ai.generativelanguage.generative_models._async_engine').setLevel(logging.WARNING)

# Add these lines to suppress OpenAI client logs
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# Get logger for main
logger = logging.getLogger(__name__)

# Set evaluator logger to show only INFO and above (removing DEBUG level)
evaluator_logger = logging.getLogger('evaluator')
evaluator_logger.setLevel(logging.INFO)

def load_dataset(dataset_path):
    with open(dataset_path, 'r') as f:
        return json.load(f)

async def main():
    parser = argparse.ArgumentParser(description='Run model tests')
    
    # Add eval-only mode arguments first
    parser.add_argument('--eval-only', 
                       action='store_true',
                       help='Run evaluation only on pre-processed responses')
    parser.add_argument('--processed-responses', 
                       help='Path to pre-processed responses file (required for eval-only mode)')
    
    # Make model-type and dataset required only if not in eval-only mode
    parser.add_argument('--model-type',
                       choices=['gemini', 'openai'],
                       help='Type of model to use (required if not in eval-only mode)')
    parser.add_argument('--dataset',
                       help='Path to test dataset (required if not in eval-only mode)')
    
    # Optional arguments
    parser.add_argument('--openai-model-name',
                       default='gpt-4o-mini',
                       help='OpenAI model name (default: gpt-4o-mini)')
    parser.add_argument('--gemini-model-id',
                       default='gemini-1.5-flash-002',
                       help='Gemini model ID (default: gemini-1.5-flash-002)')
    parser.add_argument('--openai-api-key',
                       help='OpenAI API key (optional, can use environment variable)')
    parser.add_argument('--semantic-judge-model',
                       default='gemini-1.5-pro-002',
                       help='Model to use for semantic comparison')
    parser.add_argument('--skip-evaluation',
                       action='store_true',
                       help='Skip the evaluation phase')
    
    args = parser.parse_args()

    # Validate arguments based on mode
    if not args.eval_only:
        if not args.model_type:
            parser.error("--model-type is required when not in eval-only mode")
        if not args.dataset:
            parser.error("--dataset is required when not in eval-only mode")
    elif not args.processed_responses:
        parser.error("--processed-responses is required when using eval-only mode")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("results", f"test_run_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)

    # Skip model testing for eval-only mode
    if args.eval_only:
        logger.info("\nStarting evaluation-only mode")
        logger.info(f"Loading processed responses from: {args.processed_responses}")
        
        # Run evaluation
        evaluator = Evaluator(
            test_mode=args.mode,
            semantic_judge_model_name=args.semantic_judge_model,
            run_both_tool_modes=args.run_both_tool_modes
        )
        
        await evaluator.evaluate_results(args.processed_responses)
        evaluator.save_results(results_dir)
        logger.info(f"Results saved to: {results_dir}")
        return

    logger.info(f"\nStarting test run with {args.model_type} model")
    logger.info(f"Loading dataset from: {args.dataset}")
    test_dataset = load_dataset(args.dataset)
    logger.info(f"Loaded {len(test_dataset)} test cases")

    if args.model_type == 'openai':
        if not args.openai_api_key:
            logger.error("Error: OpenAI API key is required for OpenAI model.")
            return
        logger.info(f"Initializing OpenAI model: {args.openai_model_name}")
        model = OpenAIModel(
            model_name=args.openai_model_name,
            api_key=args.openai_api_key,
            temperature=0
        )
    elif args.model_type == 'gemini':
        logger.info(f"Initializing Gemini model: {args.gemini_model_id}")
        model = GeminiModel(
            model_id=args.gemini_model_id,
            temperature=0
        )
    else:
        logger.error("Invalid model type.")
        return

    logger.info("Starting test execution...")
    tester = ModelTester(
        model=model,
        test_dataset=test_dataset,
    )
    
    # Step 1: Run tests and save raw results
    raw_results = {}
    raw_results = await tester.run_tests()

    raw_results_file = os.path.join(results_dir, "raw_responses.json")
    with open(raw_results_file, 'w') as f:
        json.dump({'test_results': raw_results}, f, indent=2)

    # Step 2: Process raw results into standardized format
    processed_results = await process_raw_responses(raw_results_file, model)
    processed_results_file = os.path.join(results_dir, "processed_responses.json")
    with open(processed_results_file, 'w') as f:
        json.dump(processed_results, f, indent=2)

    # Save test parameters
    test_parameters = {
        "timestamp": timestamp,
        "model_type": args.model_type,
        "dataset_path": args.dataset,
        "model_id": args.gemini_model_id if args.model_type == 'gemini' else args.openai_model_name,
        "semantic_judge_model": args.semantic_judge_model if not args.skip_evaluation else None,
        "generation_config": {
            "temperature": 0.0
        }
    }
    
    parameters_file = os.path.join(results_dir, "test_parameters.json")
    with open(parameters_file, 'w') as f:
        json.dump(test_parameters, f, indent=2)
    
    if not args.skip_evaluation:
        logger.info("Starting evaluation...")
        try:
            # Initialize evaluator
            evaluator = Evaluator(
                semantic_judge_model_name=args.semantic_judge_model
            )
            
            await evaluator.evaluate_results(processed_results_file)
            evaluator.save_results(results_dir)
        except ValueError as e:
            logger.error(f"Evaluation failed: {str(e)}")
            return
    else:
        logger.info("Skipping evaluation as per user request.")

        logger.info(f"Results saved to: {results_dir}")

if __name__ == "__main__":
    asyncio.run(main())