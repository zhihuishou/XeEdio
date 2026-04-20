package wrapper

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// LLMClient defines the interface for LLM interactions, enabling easy mocking in tests.
type LLMClient interface {
	// Generate calls the LLM API with a system prompt and user prompt, returning the raw text response.
	Generate(ctx context.Context, systemPrompt, userPrompt string) (string, error)
}

// DeepSeekLLMClient implements LLMClient by calling the DeepSeek API via HTTP POST.
type DeepSeekLLMClient struct {
	apiKey   string
	endpoint string
	client   *http.Client
}

// NewDeepSeekLLMClient creates a new DeepSeekLLMClient with the given API key, endpoint, and timeout.
func NewDeepSeekLLMClient(apiKey, endpoint string, timeout time.Duration) *DeepSeekLLMClient {
	return &DeepSeekLLMClient{
		apiKey:   apiKey,
		endpoint: strings.TrimRight(endpoint, "/"),
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

// chatRequest is the JSON body sent to the DeepSeek chat completions API.
type chatRequest struct {
	Model       string        `json:"model"`
	Messages    []chatMessage `json:"messages"`
	Temperature float64       `json:"temperature"`
}

// chatMessage represents a single message in the chat completions request.
type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// chatResponse is the JSON body returned by the DeepSeek chat completions API.
type chatResponse struct {
	Choices []chatChoice `json:"choices"`
}

// chatChoice represents a single choice in the API response.
type chatChoice struct {
	Message chatMessage `json:"message"`
}

// Generate calls the DeepSeek API with the given system and user prompts.
// It sends an HTTP POST to {endpoint}/v1/chat/completions and extracts
// the content from choices[0].message.content.
func (c *DeepSeekLLMClient) Generate(ctx context.Context, systemPrompt, userPrompt string) (string, error) {
	reqBody := chatRequest{
		Model: "deepseek-chat",
		Messages: []chatMessage{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: userPrompt},
		},
		Temperature: 0.7,
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("llm_wrapper: failed to marshal request: %w", err)
	}

	url := c.endpoint + "/v1/chat/completions"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, strings.NewReader(string(bodyBytes)))
	if err != nil {
		return "", fmt.Errorf("llm_wrapper: failed to create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("llm_wrapper: request failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("llm_wrapper: failed to read response body: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("llm_wrapper: API returned status %d: %s", resp.StatusCode, string(respBody))
	}

	var chatResp chatResponse
	if err := json.Unmarshal(respBody, &chatResp); err != nil {
		return "", fmt.Errorf("llm_wrapper: failed to unmarshal response: %w", err)
	}

	if len(chatResp.Choices) == 0 {
		return "", fmt.Errorf("llm_wrapper: API returned empty choices")
	}

	return chatResp.Choices[0].Message.Content, nil
}
