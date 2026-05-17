pipeline {
    agent any

    environment {
        PYTHON = 'C:\\Users\\tanma\\AppData\\Local\\Programs\\Python\\Python314\\python.exe'
        DOCKER_IMAGE = 'noteflow-app'
        DOCKERHUB_REPO = 'tanmaydixit09/noteflow-app'
        KUBECONFIG = 'C:\\Users\\tanma\\.kube\\config'
    }

    stages {

        stage('Checkout') {
            steps {
                echo '📥 Checking out code from GitHub...'
                checkout scm
            }
        }

        stage('Install Dependencies') {
            steps {
                echo '📦 Installing Python dependencies...'

                bat """
                "%PYTHON%" --version

                "%PYTHON%" -m pip install --upgrade pip

                "%PYTHON%" -m pip install -r requirements.txt

                "%PYTHON%" -m pip install pytest
                """
            }
        }

        stage('Run Tests') {
            steps {
                echo '🧪 Running tests...'

                bat """
                set TESTING=1

                "%PYTHON%" -m pytest test_app.py -v --tb=short
                """
            }
        }

        stage('Build Docker Image') {
            steps {
                echo '🐳 Building Docker image...'

                bat """
                docker build -t %DOCKER_IMAGE%:%BUILD_NUMBER% .

                docker tag %DOCKER_IMAGE%:%BUILD_NUMBER% %DOCKER_IMAGE%:latest
                """
            }
        }

        stage('Push to Docker Hub') {
            steps {
                echo '🚀 Pushing image to Docker Hub...'

                withCredentials([
                    usernamePassword(
                        credentialsId: 'dockerhub-creds',
                        usernameVariable: 'DOCKER_USER',
                        passwordVariable: 'DOCKER_PASS'
                    )
                ]) {

                    bat """
                    docker login -u %DOCKER_USER% -p %DOCKER_PASS%

                    docker tag %DOCKER_IMAGE%:latest %DOCKERHUB_REPO%:latest
                    docker tag %DOCKER_IMAGE%:latest %DOCKERHUB_REPO%:%BUILD_NUMBER%

                    docker push %DOCKERHUB_REPO%:latest
                    docker push %DOCKERHUB_REPO%:%BUILD_NUMBER%
                    """
                }
            }
        }

        stage('Deploy to Kubernetes') {
            steps {
                echo '☸️ Deploying to Kubernetes...'

                bat """
                set KUBECONFIG=%KUBECONFIG%

                kubectl config current-context

                kubectl get nodes

                kubectl apply -f k8s/deployment.yaml

                kubectl apply -f k8s/service.yaml

                kubectl rollout status deployment/noteflow-deployment
                """
            }
        }
    }

    post {

        always {
            echo '🧹 Cleaning up...'

            bat """
            docker rmi %DOCKER_IMAGE%:%BUILD_NUMBER% || ver > nul
            """
        }

        success {
            echo '✅ Pipeline completed successfully!'
        }

        failure {
            echo '❌ Pipeline failed. Check the logs above.'
        }
    }
}
