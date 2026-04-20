package harness

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"ai-video-pipeline/internal/model"
)

// mockLLMClient implements wrapper.LLMClient for testing.
type mockLLMClient struct {
	responses []string // responses returned in order
	errors    []error  // errors returned in order (nil for success)
	calls     []mockLLMCall
	callIndex int
}

type mockLLMCall struct {
	SystemPrompt string
	UserPrompt   string
}

func (m *mockLLMClient) Generate(ctx context.Context, systemPrompt, userPrompt string) (string, error) {
	m.calls = append(m.calls, mockLLMCall{SystemPrompt: systemPrompt, UserPrompt: userPrompt})
	idx := m.callIndex
	m.callIndex++

	if idx < len(m.errors) && m.errors[idx] != nil {
		return "", m.errors[idx]
	}
	if idx < len(m.responses) {
		return m.responses[idx], nil
	}
	return "", fmt.Errorf("no more mock responses")
}

func validScriptJSON() string {
	out := model.ScriptOutput{
		Scenes:     []model.Scene{{SceneID: 1, Description: "sunrise over city"}},
		Narrations: []model.Narration{{SceneID: 1, Text: "A new day begins"}},
	}
	b, _ := json.Marshal(out)
	return string(b)
}

func scriptHarnessConfig() RetryConfig {
	return RetryConfig{
		MaxRetries:    1,
		RetryInterval: 10 * time.Millisecond,
		Timeout:       5 * time.Second,
	}
}

func TestScriptHarness_Execute_Success(t *testing.T) {
	mock := &mockLLMClient{
		responses: []string{validScriptJSON()},
	}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: "morning routine"})
	output, err := h.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var result model.ScriptOutput
	if err := json.Unmarshal(output, &result); err != nil {
		t.Fatalf("failed to unmarshal output: %v", err)
	}
	if len(result.Scenes) != 1 || result.Scenes[0].Description != "sunrise over city" {
		t.Errorf("unexpected scenes: %+v", result.Scenes)
	}
	if len(result.Narrations) != 1 || result.Narrations[0].Text != "A new day begins" {
		t.Errorf("unexpected narrations: %+v", result.Narrations)
	}
}

func TestScriptHarness_Execute_InvalidInput(t *testing.T) {
	mock := &mockLLMClient{}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	_, err := h.Execute(context.Background(), []byte(`not json`))
	if err == nil {
		t.Fatal("expected error for invalid JSON input")
	}
	if !strings.Contains(err.Error(), "input validation failed") {
		t.Errorf("error should mention input validation, got: %v", err)
	}
}

func TestScriptHarness_Execute_EmptyInput(t *testing.T) {
	mock := &mockLLMClient{}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	_, err := h.Execute(context.Background(), nil)
	if err == nil {
		t.Fatal("expected error for nil input")
	}
}

func TestScriptHarness_Execute_MissingTopic(t *testing.T) {
	mock := &mockLLMClient{}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: ""})
	_, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected error for empty topic")
	}
	if !strings.Contains(err.Error(), "topic is required") {
		t.Errorf("error should mention topic, got: %v", err)
	}
}

func TestScriptHarness_Execute_FormatCorrectionRetry(t *testing.T) {
	// First response is invalid, second (corrected) is valid
	mock := &mockLLMClient{
		responses: []string{
			"This is not JSON at all",
			validScriptJSON(),
		},
	}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: "coffee culture"})
	output, err := h.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify the second call included format correction instructions
	if len(mock.calls) < 2 {
		t.Fatalf("expected at least 2 LLM calls, got %d", len(mock.calls))
	}
	secondCall := mock.calls[1]
	if !strings.Contains(secondCall.UserPrompt, "not valid JSON") {
		t.Errorf("second call should contain format correction, got: %s", secondCall.UserPrompt)
	}
	if !strings.Contains(secondCall.UserPrompt, "This is not JSON at all") {
		t.Errorf("second call should include the failed response, got: %s", secondCall.UserPrompt)
	}

	var result model.ScriptOutput
	if err := json.Unmarshal(output, &result); err != nil {
		t.Fatalf("failed to unmarshal output: %v", err)
	}
	if len(result.Scenes) != 1 {
		t.Errorf("expected 1 scene, got %d", len(result.Scenes))
	}
}

func TestScriptHarness_Execute_MarkdownCodeBlock(t *testing.T) {
	// LLM wraps JSON in markdown code block
	wrapped := "```json\n" + validScriptJSON() + "\n```"
	mock := &mockLLMClient{
		responses: []string{wrapped},
	}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: "travel vlog"})
	output, err := h.Execute(context.Background(), input)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	var result model.ScriptOutput
	if err := json.Unmarshal(output, &result); err != nil {
		t.Fatalf("failed to unmarshal output: %v", err)
	}
	if len(result.Scenes) != 1 {
		t.Errorf("expected 1 scene, got %d", len(result.Scenes))
	}
}

func TestScriptHarness_Execute_LLMError(t *testing.T) {
	mock := &mockLLMClient{
		errors: []error{
			fmt.Errorf("API unavailable"),
			fmt.Errorf("API unavailable"),
		},
	}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: "test"})
	_, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected error when LLM fails")
	}
}

func TestScriptHarness_Execute_ValidationFailure(t *testing.T) {
	// Return JSON that parses but fails validation (empty scenes)
	invalidOutput := `{"scenes":[],"narrations":[]}`
	mock := &mockLLMClient{
		responses: []string{
			invalidOutput,
			invalidOutput,
		},
	}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)

	input, _ := json.Marshal(model.PipelineInput{Topic: "test"})
	_, err := h.Execute(context.Background(), input)
	if err == nil {
		t.Fatal("expected validation error")
	}
	if !strings.Contains(err.Error(), "validation failed") {
		t.Errorf("error should mention validation, got: %v", err)
	}
}

func TestExtractJSON(t *testing.T) {
	tests := []struct {
		name  string
		input string
		want  string
	}{
		{
			name:  "plain JSON",
			input: `{"key":"value"}`,
			want:  `{"key":"value"}`,
		},
		{
			name:  "markdown json block",
			input: "```json\n{\"key\":\"value\"}\n```",
			want:  "\n{\"key\":\"value\"}\n",
		},
		{
			name:  "markdown plain block",
			input: "```\n{\"key\":\"value\"}\n```",
			want:  "\n{\"key\":\"value\"}\n",
		},
		{
			name:  "surrounded by text",
			input: "Here is the result: {\"key\":\"value\"} hope this helps",
			want:  `{"key":"value"}`,
		},
		{
			name:  "no JSON",
			input: "no json here",
			want:  "no json here",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractJSON(tt.input)
			if got != tt.want {
				t.Errorf("extractJSON(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestScriptHarness_Name(t *testing.T) {
	mock := &mockLLMClient{}
	h := NewScriptHarness(scriptHarnessConfig(), testLogger(), mock)
	if h.Name() != "script" {
		t.Errorf("Name() = %q, want %q", h.Name(), "script")
	}
}
