class Opentraces < Formula
  include Language::Python::Virtualenv

  desc "Crowdsource agent traces to HuggingFace Hub"
  homepage "https://opentraces.ai"
  url "https://files.pythonhosted.org/packages/source/o/opentraces/opentraces-0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"

  depends_on "python@3.12"

  # NOTE: After the first PyPI publish, generate resource blocks with:
  #   pip install homebrew-pypi-poet
  #   poet opentraces
  # Then paste the output here, replacing this comment.

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/opentraces --version")
    assert_match version.to_s, shell_output("#{bin}/ot --version")
  end
end
