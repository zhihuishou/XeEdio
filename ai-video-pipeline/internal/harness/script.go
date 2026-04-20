package harness

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"

	"ai-video-pipeline/internal/model"
	"ai-video-pipeline/internal/wrapper"
)

const scriptSystemPrompt = `You are a professional short video script writer. Given a topic, generate a script with scenes and narrations in JSON format.

Your response MUST be a valid JSON object with the following structure:
{
  "scenes": [
    {"scene_id": 1, "description": "scene description"},
    {"scene_id": 2, "description": "scene description"}
  ],
  "narrations": [
    {"scene_id": 1, "text": "narration text for scene 1"},
    {"scene_id": 2, "text": "narration text for scene 2"}
  ]
}

Rules:
- Each scene must have a unique positive integer scene_id and a non-empty description.
- Each narration must have a scene_id matching a scene and non-empty text.
- The number of scenes and narrations must be equal.
- Return ONLY the JSON object, no extra text.`

const formatCorrectionPrompt = `Your previous response was not valid JSON or did not match the required format. The response was:

%s

Please respond with ONLY a valid JSON object matching this exact structure:
{
  "scenes": [
    {"scene_id": 1, "description": "scene description"}
  ],
  "narrations": [
    {"scene_id": 1, "text": "narration text"}
  ]
}

Return ONLY the JSON object, no markdown, no extra text.`

// ScriptHarness wraps LLM calls to generate video scripts.
// It combines BaseHarness (retry/timeout/logging) with an LLMClient.
type ScriptHarness struct {
	BaseHarness
	llm wrapper.LLMClient
}

// NewScriptHarness creates a new ScriptHarness.
func NewScriptHarness(cfg RetryConfig, logger *slog.Logger, llm wrapper.LLMClient) *ScriptHarness {
	return &ScriptHarness{
		BaseHarness: NewBaseHarness("script", cfg, logger),
		llm:         llm,
	}
}

// Execute receives PipelineInput JSON (containing a topic), calls the LLM to generate
// a script, parses and validates the response, and returns ScriptOutput JSON.
// It uses BaseHarness.ExecuteWithRetry for overall retry/timeout logic.
func (s *ScriptHarness) Execute(ctx context.Context, input []byte) ([]byte, error) {
	start := time.Now()
	var output []byte

	// Validate input JSON
	if err := ValidateJSONInput(input); err != nil {
		s.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Parse input to extract topic
	var pipelineInput model.PipelineInput
	if err := json.Unmarshal(input, &pipelineInput); err != nil {
		err = fmt.Errorf("script: failed to parse input: %w", err)
		s.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}
	if pipelineInput.Topic == "" {
		err := fmt.Errorf("script: topic is required")
		s.LogExecution(input, nil, time.Since(start), err)
		return nil, err
	}

	// Use ExecuteWithRetry for overall retry/timeout logic
	output, err := s.ExecuteWithRetry(ctx, func(ctx context.Context) ([]byte, error) {
		return s.generateScript(ctx, pipelineInput.Topic)
	}, nil)

	s.LogExecution(input, output, time.Since(start), err)
	return output, err
}

// generateScript calls the LLM, parses the response, and validates it.
// On parse failure, it retries with format correction instructions.
func (s *ScriptHarness) generateScript(ctx context.Context, topic string) ([]byte, error) {
	userPrompt := fmt.Sprintf("Please generate a short video script for the following topic: %s", topic)

	// First attempt: call LLM
	response, err := s.llm.Generate(ctx, scriptSystemPrompt, userPrompt)
	if err != nil {
		return nil, fmt.Errorf("script: LLM call failed: %w", err)
	}

	// Try to parse the LLM response
	scriptOutput, err := parseLLMResponse(response)
	if err != nil {
		// Format correction retry: call LLM again with correction instructions
		correctionUser := fmt.Sprintf(formatCorrectionPrompt, response)
		correctedResponse, retryErr := s.llm.Generate(ctx, scriptSystemPrompt, correctionUser)
		if retryErr != nil {
			return nil, fmt.Errorf("script: format correction LLM call failed: %w", retryErr)
		}

		scriptOutput, err = parseLLMResponse(correctedResponse)
		if err != nil {
			return nil, fmt.Errorf("script: failed to parse corrected LLM response: %w", err)
		}
	}

	// Validate the parsed ScriptOutput
	if err := scriptOutput.Validate(); err != nil {
		return nil, fmt.Errorf("script: %w", err)
	}

	// Marshal to JSON
	result, err := model.MarshalScriptOutput(scriptOutput)
	if err != nil {
		return nil, fmt.Errorf("script: failed to marshal output: %w", err)
	}

	return result, nil
}

// parseLLMResponse attempts to parse the raw LLM response string as ScriptOutput JSON.
// It tries to extract JSON from the response in case the LLM wraps it in markdown code blocks.
func parseLLMResponse(response string) (*model.ScriptOutput, error) {
	cleaned := extractJSON(response)
	scriptOutput, err := model.UnmarshalScriptOutput([]byte(cleaned))
	if err != nil {
		return nil, fmt.Errorf("failed to parse LLM response: %w", err)
	}
	return scriptOutput, nil
}

// extractJSON attempts to extract a JSON object from a string that may contain
// markdown code blocks or other surrounding text.
func extractJSON(s string) string {
	// Try to find JSON within markdown code blocks: ```json ... ``` or ``` ... ```
	if start := findSubstring(s, "```json"); start >= 0 {
		content := s[start+7:]
		if end := findSubstring(content, "```"); end >= 0 {
			return content[:end]
		}
	}
	if start := findSubstring(s, "```"); start >= 0 {
		content := s[start+3:]
		if end := findSubstring(content, "```"); end >= 0 {
			return content[:end]
		}
	}

	// Try to find a JSON object by locating the first { and last }
	firstBrace := -1
	lastBrace := -1
	for i, c := range s {
		if c == '{' && firstBrace == -1 {
			firstBrace = i
		}
		if c == '}' {
			lastBrace = i
		}
	}
	if firstBrace >= 0 && lastBrace > firstBrace {
		return s[firstBrace : lastBrace+1]
	}

	return s
}

// findSubstring returns the index of the first occurrence of substr in s, or -1.
func findSubstring(s, substr string) int {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return i
		}
	}
	return -1
}
