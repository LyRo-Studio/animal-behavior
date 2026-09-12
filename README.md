# LyRo Studio Web App Template

Starter template for LyRo Studio Web Applications.

This repository is maintained as a GitHub Template Repository and is intended to be used as the starting point for new LyRo Studio web application projects.

## Prerequisites

Before creating a new project, make sure the following tools are installed and configured:

- Git
- GitHub CLI (`gh`)
- GitHub CLI authentication (`gh auth login`)

## Create a new project from the GitHub template

Create a new private GitHub repository from this template and clone it locally:

```bash
gh repo create LyRo-Studio/<project-name> \
  --private \
  --template LyRo-Studio/webapp-template \
  --clone
```

Replace `<project-name>` with the name of the new project.

For example:

```bash
gh repo create LyRo-Studio/customer-portal \
  --private \
  --template LyRo-Studio/webapp-template \
  --clone
```

This creates a new GitHub repository with its own Git history based on the current contents of:

`LyRo-Studio/webapp-template`

and clones the new repository into the current local directory.

Move into the project directory:

```bash
cd <project-name>
```

The project is now ready for the project-specific discovery and development workflow.

## Optional PowerShell helper

If new web application projects are created frequently, a PowerShell helper can be added to your PowerShell profile:

```powershell
function create-new-webapp-project {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    gh repo create "LyRo-Studio/$Name" `
        --private `
        --template "LyRo-Studio/webapp-template" `
        --clone

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not create project '$Name'."
        return
    }

    Set-Location $Name

    Write-Host ""
    Write-Host "Project '$Name' created successfully."
    Write-Host "Current directory: $(Get-Location)"
}
```

A new project can then be created with:

```powershell
create-new-webapp-project customer-portal
```

The helper:

1. creates a new private repository in the `LyRo-Studio` GitHub organisation;
2. uses `LyRo-Studio/webapp-template` as the template;
3. clones the newly created repository locally;
4. moves the terminal into the new project directory.

## Project-specific README

This README belongs to the organisation template.

After creating a new project, replace this README with documentation describing the actual web application.
